#!/usr/bin/env python3
"""
OpenTelemetry Observability Script

This script collects monitoring data from Prometheus and sends it to Loki.
It extracts information about monitoring components, their images, and Grafana usage.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Union, Tuple

import requests

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def query_prometheus(prometheus_url: str, query: str) -> Optional[Dict[str, Any]]:
    """
    Query Prometheus and return the response.
    
    Args:
        prometheus_url: URL of the Prometheus instance
        query: PromQL query to execute
        
    Returns:
        JSON response from Prometheus or None if the query fails
    """
    try:
        logger.info(f"Querying Prometheus with query: {query}")
        response = requests.get(
            f"{prometheus_url}/api/v1/query",
            params={'query': query}
        )
        response.raise_for_status()
        logger.debug("Successfully received response from Prometheus")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Prometheus: {e}")
        return None


def extract_monitoring_images(prometheus_response: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Dict[str, Dict[str, str]]]]]:
    """
    Extract image information for monitoring components and daemonsets.
    
    Args:
        prometheus_response: Response from Prometheus containing container information
        
    Returns:
        Dictionary with image information categorized by cluster and namespace, then by 'main' and 'monitoring'
    """
    try:
        logger.info("Extracting monitoring images from Prometheus response")
        images = {}
        
        for result in prometheus_response['data']['result']:
            container = result['metric']['container']
            image = result['metric']['image']
            pod = result['metric']['pod']
            cluster = result['metric']['cluster']
            namespace = result['metric']['namespace']
            
            # Initialize cluster and namespace structure if not exists
            if cluster not in images:
                images[cluster] = {}
            if namespace not in images[cluster]:
                images[cluster][namespace] = {
                    'main': {},
                    'monitoring': {}
                }
            
            # Determine category based on pod name
            category = 'monitoring' if pod.startswith('duplo-monitoring-') else 'main'
            
            # Map container names to their service names
            service_name = None
            
            # Check for special cases first
            if container in ['ingester', 'distributor', 'compactor', 'querier', 'query-frontend', 'ruler', 'store-gateway', 'metrics-generator']:
                # Check if it's tempo or mimir
                if 'tempo' in image:
                    service_name = 'tempo'
                else:
                    service_name = 'mimir'
            elif container == 'alloy':
                # Check alloy type based on pod name
                if 'profiles' in pod:
                    service_name = 'alloy-profiles'
                elif 'logs' in pod:
                    service_name = 'alloy-logs'
                elif 'events' in pod:
                    service_name = 'alloy-events'
                else:
                    service_name = 'alloy-core'
            elif container == 'manager':
                service_name = 'opentelemetry-operator'
                # This is run on each cluster, so we need to categorize it as monitoring
                category = 'monitoring'
            else:
                # Default case: use container name as service name
                service_name = container
            
            if service_name:
                images[cluster][namespace][category][service_name] = image
                logger.debug(f"Added image for service {service_name} in category {category} for cluster {cluster} namespace {namespace}")
                
        logger.info(f"Successfully extracted images for {sum(len(ns['main']) + len(ns['monitoring']) for cluster in images.values() for ns in cluster.values())} services")
        return images
    except (KeyError, IndexError) as e:
        logger.error(f"Error extracting monitoring images: {e}")
        return None


def send_to_loki(
    job: str,
    source: str,
    type: str,
    values: List[List[str]]
) -> None:
    """
    Send data to Loki in JSON format with stream labels.
    
    Args:
        job: Job name for the stream
        source: Source of the data
        type: Type of data
        values: List of [timestamp, value] pairs to send
    """
    logger.info(f"Sending {type} data to Loki")
    
    # Get Loki credentials and URL from environment
    loki_url = os.getenv('LOKI_URL')
    loki_username = os.getenv('LOKI_USERNAME')
    loki_password = os.getenv('LOKI_PASSWORD')
    
    # Get static labels from environment variables
    cluster = os.getenv('CLUSTER', '')
    namespace = os.getenv('NAMESPACE', '')
    customer = os.getenv('CUSTOMER', '')
    environment = os.getenv('ENVIRONMENT', '')
    duplo_url = os.getenv('DUPLO_URL', '')
    job_version = os.getenv('JOB_VERSION', '')
    
    # Prepare the stream with labels
    stream = {
        "job": job,
        "source": source,
        "type": type,
        "cluster": cluster,
        "namespace": namespace,
        "customer": customer,
        "environment": environment,
        "duplo_url": duplo_url,
        "job_version": job_version
    }
    
    # Create the payload
    payload = {
        "streams": [
            {
                "stream": stream,
                "values": values
            }
        ]
    }
    
    # Log the payload for debugging
    logger.debug(f"Loki payload: {json.dumps(payload, indent=2)}")

    try:
        headers = {'Content-Type': 'application/json'}
        
        # Add authentication if credentials are provided
        auth = None
        if loki_username and loki_password:
            auth = (loki_username, loki_password)
            logger.debug("Using basic authentication for Loki")
        
        response = requests.post(
            f"{loki_url}/loki/api/v1/push",
            json=payload,
            headers=headers,
            auth=auth
        )
        response.raise_for_status()
        logger.info(f"Successfully sent {type} data to Loki")
        logger.debug(f"Loki response status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending data to Loki: {e}")


def format_and_send_image_data(images: Dict[str, Dict[str, Dict[str, Dict[str, str]]]], labels: Dict[str, str]) -> None:
    """
    Format and send image data to Loki.
    
    Args:
        images: Dictionary containing image information for different services
        labels: Dictionary containing additional labels
    """
    current_time = int(time.time() * 1000000000)  # Current time in nanoseconds
    
    # Process each cluster and namespace
    for cluster, namespaces in images.items():
        for namespace, categories in namespaces.items():
            # Process main images
            if categories['main']:
                values = [
                    [str(current_time), json.dumps({
                        "metadata": {
                            "cluster": cluster,
                            "namespace": namespace
                        },
                        "spec": categories['main']
                    })]
                ]
                send_to_loki(
                    "monitoring_images",
                    "prometheus",
                    "main",
                    values
                )
            
            # Process monitoring images
            if categories['monitoring']:
                values = [
                    [str(current_time), json.dumps({
                        "metadata": {
                            "cluster": cluster,
                            "namespace": namespace
                        },
                        "spec": categories['monitoring']
                    })]
                ]
                send_to_loki(
                    "monitoring_images",
                    "prometheus",
                    "monitoring",
                    values
                )


def format_and_send_grafana_usage_data(grafana_usage: Dict[str, int], labels: Dict[str, str]) -> None:
    """
    Format and send Grafana usage data to Loki.
    
    Args:
        grafana_usage: Dictionary containing Grafana datasource usage information
        labels: Dictionary containing additional labels
    """
    current_time = int(time.time() * 1000000000)  # Current time in nanoseconds
    
    values = [
        [str(current_time), json.dumps(grafana_usage)]
    ]
    
    send_to_loki(
        "grafana_usage",
        "prometheus",
        "datasource_usage",
        values
    )


def validate_environment_variables() -> Tuple[bool, Dict[str, str], List[str]]:
    """
    Validate required environment variables and return configuration.
    
    Returns:
        Tuple containing:
        - Boolean indicating if validation was successful
        - Dictionary of labels from environment variables
        - List of missing environment variables
    """
    # Configuration from environment variables
    prometheus_url = os.getenv('PROMETHEUS_URL')
    loki_url = os.getenv('LOKI_URL')
    
    # Additional labels from environment variables
    labels = {
        'cluster': os.getenv('CLUSTER', ''),
        'namespace': os.getenv('NAMESPACE', ''),
        'customer': os.getenv('CUSTOMER', ''),
        'environment': os.getenv('ENVIRONMENT', ''),
        'duplo_url': os.getenv('DUPLO_URL', ''),
        'job_version': os.getenv('JOB_VERSION', ''),
        # Ability to filter the custom OTEL namespace for the query
        'namespace_filter': os.getenv('NAMESPACE_FILTER', '.*otel.*')
    }
    
    # Validate required environment variables
    required_vars = [
        'PROMETHEUS_URL', 'LOKI_URL', 'CLUSTER', 'NAMESPACE', 
        'CUSTOMER', 'ENVIRONMENT', 'DUPLO_URL'
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return False, labels, missing_vars
    
    return True, labels, []


def collect_image_versions(prometheus_url: str, labels: Dict[str, str]) -> Optional[Dict[str, Dict[str, Dict[str, Dict[str, str]]]]]:
    """
    Collect image versions for monitoring components from Prometheus.
    
    Args:
        prometheus_url: URL of the Prometheus instance
        labels: Dictionary containing additional labels
        
    Returns:
        Dictionary with image information categorized by cluster and namespace, then by 'main' and 'monitoring',
        or None if collection fails
    """
    logger.info("Collecting image versions from Prometheus")
    
    # Get namespace filter from labels with fallback to default
    namespace_filter = labels.get('namespace_filter', '.*otel.*')
    
    # PromQL query for all components
    query = f'''
    count by(cluster, namespace, container, image, pod) (
      kube_pod_container_info{{namespace=~"{namespace_filter}", container!~"config-reloader|loki-sc-rules|memcached|gateway|exporter|kube-rbac-proxy|nginx|pushgateway"}}
    )
    '''
    
    # Query Prometheus for container images
    prometheus_response = query_prometheus(prometheus_url, query)
    if not prometheus_response:
        logger.error("Failed to query Prometheus for image versions")
        return None
    
    # Extract monitoring images
    images = extract_monitoring_images(prometheus_response)
    if not images:
        logger.error("Failed to extract monitoring images from Prometheus response")
        return None
    
    logger.info("Successfully collected image versions")
    return images


def collect_grafana_usage(prometheus_url: str) -> Dict[str, int]:
    """
    Collect Grafana datasource usage information from Prometheus.
    
    Args:
        prometheus_url: URL of the Prometheus instance
        
    Returns:
        Dictionary with datasource names as keys and usage counts as values
    """
    logger.info("Collecting Grafana usage data")
    
    # PromQL query for Grafana datasource usage
    query = 'sum by (datasource) (increase(grafana_datasource_request_duration_seconds_count[24h]))'
    
    # Query Prometheus for Grafana usage
    data = query_prometheus(prometheus_url, query)
    if not data:
        logger.warning("Could not fetch Grafana usage data from Prometheus")
        return {}
    
    # Extract datasource usage data
    usage_data = {}
    try:
        if 'data' in data and 'result' in data['data']:
            for result in data['data']['result']:
                datasource = result['metric']['datasource']
                value = float(result['value'][1])  # Get the instant value
                usage_data[datasource] = round(value)  # Round to nearest integer
                logger.debug(f"Datasource {datasource} usage: {usage_data[datasource]}")
        
        logger.info(f"Successfully collected usage data for {len(usage_data)} datasources")
        return usage_data
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Error processing Grafana usage data: {e}")
        return {}


def collect_and_send_version_data(prometheus_url: str, labels: Dict[str, str]) -> None:
    """
    Collect image versions from Prometheus and send them to Loki.
    
    Args:
        prometheus_url: URL of the Prometheus instance
        labels: Dictionary containing additional labels
    """
    logger.info("Collecting and sending image data")
    
    # Collect image versions
    images = collect_image_versions(prometheus_url, labels)
    if not images:
        logger.error("Failed to collect image versions")
        return
    
    # Format and send image data
    format_and_send_image_data(images, labels)
    logger.info("Completed image data collection and sending")


def collect_and_send_grafana_usage(prometheus_url: str, labels: Dict[str, str]) -> None:
    """
    Collect Grafana usage data from Prometheus and send it to Loki.
    
    Args:
        prometheus_url: URL of the Prometheus instance
        labels: Dictionary containing additional labels
    """
    logger.info("Collecting and sending Grafana usage data")
    
    # Collect Grafana usage
    grafana_usage = collect_grafana_usage(prometheus_url)
    
    # Format and send Grafana usage data
    format_and_send_grafana_usage_data(grafana_usage, labels)
    logger.info("Completed Grafana usage data collection and sending")

def collect_and_send_otel_pod_node_usage(prometheus_url: str, labels: dict) -> None:
    """
    Collects 24h pod/node resource stats for all clusters/namespaces present in Prometheus data,
    and pushes a Loki log entry per (cluster, namespace), with pods/nodes as lists of dicts.
    """
    logger.info("Collecting 24h OTEL pod/node usage statistics")

    namespace_regex = labels.get('namespace_filter', '.*otel.*')

    # 1. Pods to exclude: DaemonSet/Job pods
    def excluded_pods_by_owner_kind(kind):
        query = f'kube_pod_owner{{namespace=~"{namespace_regex}",owner_kind="{kind}"}}'
        response = query_prometheus(prometheus_url, query) or {}
        return {(m['metric'].get('cluster'), m['metric'].get('namespace'), m['metric'].get('pod'))
                for m in response.get("data", {}).get("result", [])}

    daemonset_pods = excluded_pods_by_owner_kind("DaemonSet")
    job_pods = excluded_pods_by_owner_kind("Job")
    excluded_pods = daemonset_pods | job_pods

    # 2. Pod to node mapping
    pod_to_node = {}
    pod_node_query = f'kube_pod_info{{namespace=~"{namespace_regex}"}}'
    pod_node_response = query_prometheus(prometheus_url, pod_node_query) or {}
    for record in pod_node_response.get("data", {}).get("result", []):
        cluster = record['metric'].get('cluster')
        namespace = record['metric'].get('namespace')
        pod_name = record['metric'].get('pod')
        node_name = record['metric'].get('node')
        if cluster and namespace and pod_name and node_name:
            pod_to_node[(cluster, namespace, pod_name)] = node_name

    # 3. Pod resource requests/limits
    def extract_pod_resource_usage(prometheus_query):
        response = query_prometheus(prometheus_url, prometheus_query)
        result = {}
        if response and 'result' in response.get('data', {}):
            for record in response['data']['result']:
                cluster = record['metric'].get('cluster')
                namespace = record['metric'].get('namespace')
                pod_name = record['metric'].get('pod')
                value = float(record['value'][1])
                if cluster and namespace and pod_name:
                    result[(cluster, namespace, pod_name)] = value
        return result
    cpu_request = extract_pod_resource_usage(f'sum by(pod,namespace,cluster) (kube_pod_container_resource_requests{{resource="cpu",namespace=~"{namespace_regex}"}})')
    mem_request = extract_pod_resource_usage(f'sum by(pod,namespace,cluster) (kube_pod_container_resource_requests{{resource="memory",namespace=~"{namespace_regex}"}})')
    cpu_limit = extract_pod_resource_usage(f'sum by(pod,namespace,cluster) (kube_pod_container_resource_limits{{resource="cpu",namespace=~"{namespace_regex}"}})')
    mem_limit = extract_pod_resource_usage(f'sum by(pod,namespace,cluster) (kube_pod_container_resource_limits{{resource="memory",namespace=~"{namespace_regex}"}})')

    # 4. Pod 24h usage
    label_filter = f'namespace=~"{namespace_regex}",container!="",container!="POD"'
    promql_templates = {
        "cpu_avg": f'avg by (pod,namespace,cluster) (avg_over_time(rate(container_cpu_usage_seconds_total{{{label_filter}}}[5m])[24h:5m]))',
        "cpu_min": f'min by (pod,namespace,cluster) (min_over_time(rate(container_cpu_usage_seconds_total{{{label_filter}}}[5m])[24h:5m]))',
        "cpu_max": f'max by (pod,namespace,cluster) (max_over_time(rate(container_cpu_usage_seconds_total{{{label_filter}}}[5m])[24h:5m]))',
        "mem_avg": f'avg by (pod,namespace,cluster) (avg_over_time(container_memory_rss{{{label_filter}}}[24h]))',
        "mem_min": f'min by (pod,namespace,cluster) (min_over_time(container_memory_rss{{{label_filter}}}[24h]))',
        "mem_max": f'max by (pod,namespace,cluster) (max_over_time(container_memory_rss{{{label_filter}}}[24h]))',
    }
    pod_usage_stats = {k: query_prometheus(prometheus_url, query) for k, query in promql_templates.items()}

    def usage_stat(stat, cluster, pod_name, namespace):
        for record in (pod_usage_stats[stat] or {}).get('data', {}).get('result', []):
            m = record.get('metric', {})
            if m.get('cluster') == cluster and m.get('pod') == pod_name and m.get('namespace') == namespace:
                return float(record['value'][1])
        return None

    pods_by_namespace = {}
    nodes_by_namespace = {}

    for record in (pod_usage_stats['cpu_avg'] or {}).get('data', {}).get('result', []):
        cluster = record['metric'].get('cluster')
        namespace = record['metric'].get('namespace')
        pod_name = record['metric'].get('pod')
        if not all([cluster, namespace, pod_name]) or (cluster, namespace, pod_name) in excluded_pods:
            continue
        node_name = pod_to_node.get((cluster, namespace, pod_name))
        if not node_name:
            continue
        ns_key = (cluster, namespace)
        nodes_by_namespace.setdefault(ns_key, set()).add(node_name)
        pods_by_namespace.setdefault(ns_key, [])
        pod_info = {
            "pod": pod_name,
            "node": node_name,
            "cpu_millicores_avg": round(usage_stat('cpu_avg', cluster, pod_name, namespace)*1000, 4) if usage_stat('cpu_avg', cluster, pod_name, namespace) is not None else None,
            "cpu_millicores_min": round(usage_stat('cpu_min', cluster, pod_name, namespace)*1000, 4) if usage_stat('cpu_min', cluster, pod_name, namespace) is not None else None,
            "cpu_millicores_max": round(usage_stat('cpu_max', cluster, pod_name, namespace)*1000, 4) if usage_stat('cpu_max', cluster, pod_name, namespace) is not None else None,
            "memory_MB_avg": round(usage_stat('mem_avg', cluster, pod_name, namespace) / (1024 * 1024), 3) if usage_stat('mem_avg', cluster, pod_name, namespace) is not None else None,
            "memory_MB_min": round(usage_stat('mem_min', cluster, pod_name, namespace) / (1024 * 1024), 3) if usage_stat('mem_min', cluster, pod_name, namespace) is not None else None,
            "memory_MB_max": round(usage_stat('mem_max', cluster, pod_name, namespace) / (1024 * 1024), 3) if usage_stat('mem_max', cluster, pod_name, namespace) is not None else None,
            "cpu_millicores_request": round(cpu_request.get((cluster, namespace, pod_name), 0) * 1000, 2) if cpu_request.get((cluster, namespace, pod_name)) is not None else None,
            "cpu_millicores_limit": round(cpu_limit.get((cluster, namespace, pod_name), 0) * 1000, 2) if cpu_limit.get((cluster, namespace, pod_name)) is not None else None,
            "memory_MB_request": round(mem_request.get((cluster, namespace, pod_name), 0) / (1024 * 1024), 2) if mem_request.get((cluster, namespace, pod_name)) is not None else None,
            "memory_MB_limit": round(mem_limit.get((cluster, namespace, pod_name), 0) / (1024 * 1024), 2) if mem_limit.get((cluster, namespace, pod_name)) is not None else None,
        }
        pods_by_namespace[ns_key].append(pod_info)

    all_ns_keys = sorted(set(list(nodes_by_namespace.keys()) + list(pods_by_namespace.keys())))

    def node_resource_stats(prometheus_url, node_name, cluster_name, interval="[24h:5m]"):
        cpu_avg_query = f'''
            avg_over_time((sum without (mode) (
                avg without (cpu) (
                    rate(node_cpu_seconds_total{{instance="{node_name}", cluster="{cluster_name}", job=~"integrations/(node_exporter|unix)", mode!="idle"}}[2m])
                )
            ) * 100){interval})
        '''
        cpu_min_query = cpu_avg_query.replace("avg_over_time", "min_over_time")
        cpu_max_query = cpu_avg_query.replace("avg_over_time", "max_over_time")
        mem_avg_query = f'''
            avg_over_time((100 - (
                avg by (instance) (node_memory_MemAvailable_bytes{{instance="{node_name}", cluster="{cluster_name}", job=~"integrations/(node_exporter|unix)"}})
                /
                avg by (instance) (node_memory_MemTotal_bytes{{instance="{node_name}", cluster="{cluster_name}", job=~"integrations/(node_exporter|unix)"}})
                * 100
            )){interval})
        '''
        mem_min_query = mem_avg_query.replace("avg_over_time", "min_over_time")
        mem_max_query = mem_avg_query.replace("avg_over_time", "max_over_time")
        def get_stat(query):
            data = query_prometheus(prometheus_url, query)
            try:
                if data and 'result' in data['data'] and data['data']['result']:
                    return float(data['data']['result'][0]['value'][1])
            except Exception:
                return None
            return None
        return {
            "cpu_percent_avg": get_stat(cpu_avg_query),
            "cpu_percent_min": get_stat(cpu_min_query),
            "cpu_percent_max": get_stat(cpu_max_query),
            "memory_percent_avg": get_stat(mem_avg_query),
            "memory_percent_min": get_stat(mem_min_query),
            "memory_percent_max": get_stat(mem_max_query),
        }

    import time
    current_time_ns = str(int(time.time() * 1e9))
    sent_count = 0
    for cluster, namespace in all_ns_keys:
        pods = pods_by_namespace.get((cluster, namespace), [])
        nodes = sorted(list({pod["node"] for pod in pods}))
        node_list = []
        for node_name in nodes:
            stats = node_resource_stats(prometheus_url, node_name, cluster)
            node_info = {"node": node_name}
            node_info.update({k: round(v, 3) if v is not None else None for k, v in stats.items()})
            node_list.append(node_info)
        otel_node_count = len(nodes)
        entry = {
            "metadata": {
                "cluster": cluster,
                "namespace": namespace,
            },
            "spec": {
                "otel_node_count": otel_node_count,
                "pods": pods,
                "nodes": node_list,
            }
        }
        send_to_loki(
            "otel_resource_usage",
            "prometheus",
            "otel_combined_usage_24h_per_ns",
            [[current_time_ns, json.dumps(entry)]]
        )
        logger.info(f"Sent usage for cluster={cluster} namespace={namespace}: "
                    f"{len(pods)} pods, {otel_node_count} nodes")
        sent_count += 1

    if sent_count == 0:
        logger.warning("No Loki messages were sent! All data may have been empty or filtered out.")

def main() -> None:
    """
    Main function that orchestrates the monitoring data collection process.
    
    This function:
    1. Retrieves configuration from environment variables
    2. Validates required environment variables
    3. Collects and sends image data
    4. Collects and sends Grafana usage data
    """
    logger.info("Starting monitoring data collection")
    
    # Validate environment variables
    is_valid, labels, missing_vars = validate_environment_variables()
    if not is_valid:
        return
    
    # Get configuration from environment
    prometheus_url = os.getenv('PROMETHEUS_URL')
    
    # Collect and send data to Loki
    collect_and_send_version_data(prometheus_url, labels)
    collect_and_send_grafana_usage(prometheus_url, labels)
    collect_and_send_otel_pod_node_usage(prometheus_url, labels)
    
    logger.info("Completed monitoring data collection")


if __name__ == "__main__":
    main() 
