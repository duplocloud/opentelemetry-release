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
    Collects 24-hour average, min, max for OTEL pod/node CPU and memory.
    Reports unique nodes per-namespace and total, and sends to Loki.
    Uses per-node queries to ensure label matching for node metrics.
    """
    logger.info("Collecting 24h OTEL pod/node usage statistics")

    namespace_filter = labels.get('namespace_filter', '.*otel.*')

    # 1. Exclude DaemonSet/Job pods
    def get_owned_pods(kind):
        q = (
            f'kube_pod_owner{{namespace=~"{namespace_filter}",owner_kind="{kind}"}}'
        )
        r = query_prometheus(prometheus_url, q) or {}
        return {i['metric'].get('pod') for i in r.get("data", {}).get("result", [])}
    ds_pods = get_owned_pods("DaemonSet")
    job_pods = get_owned_pods("Job")
    excluded_pods = ds_pods | job_pods

    # 2. Map pod->node (canonical hostname)
    pod_node_map = {}
    pod_node_resp = query_prometheus(
        prometheus_url, f'kube_pod_info{{namespace=~"{namespace_filter}"}}'
    ) or {}
    for r in pod_node_resp.get("data", {}).get("result", []):
        pod = r['metric'].get('pod')
        node = r['metric'].get('node')
        if pod and node:
            pod_node_map[pod] = node

    # 3. Gather rolling 24h stats per pod (skip excluded/unknown)
    pf = f'namespace=~"{namespace_filter}",container!="",container!="POD"'
    pod_proms = {
        "cpu_avg": f'avg by (pod,namespace) (avg_over_time(rate(container_cpu_usage_seconds_total{{{pf}}}[5m])[24h:5m]))',
        "cpu_min": f'min by (pod,namespace) (min_over_time(rate(container_cpu_usage_seconds_total{{{pf}}}[5m])[24h:5m]))',
        "cpu_max": f'max by (pod,namespace) (max_over_time(rate(container_cpu_usage_seconds_total{{{pf}}}[5m])[24h:5m]))',
        "mem_avg": f'avg by (pod,namespace) (avg_over_time(container_memory_rss{{{pf}}}[24h]))',
        "mem_min": f'min by (pod,namespace) (min_over_time(container_memory_rss{{{pf}}}[24h]))',
        "mem_max": f'max by (pod,namespace) (max_over_time(container_memory_rss{{{pf}}}[24h]))',
    }
    pod_stats = {k: query_prometheus(prometheus_url, q) for k, q in pod_proms.items()}

    # Helper for per-pod, keyed-stat lookup
    def podval(stat, pod, ns):
        for r in (pod_stats[stat] or {}).get('data', {}).get('result', []):
            m = r.get('metric', {})
            if m.get('pod') == pod and m.get('namespace') == ns:
                return float(r['value'][1])
        return None

    pod_usage = {}
    used_nodes_per_ns = {}
    for r in (pod_stats['cpu_avg'] or {}).get('data', {}).get('result', []):
        pod = r['metric'].get('pod')
        ns = r['metric'].get('namespace')
        if not pod or not ns or pod in excluded_pods:
            continue
        node = pod_node_map.get(pod)
        if not node:
            continue
        used_nodes_per_ns.setdefault(ns, set()).add(node)
        pod_usage.setdefault(ns, {})
        pod_usage[ns][pod] = {
            "node": node,
            "cpu_millicores_avg": round(podval('cpu_avg', pod, ns)*1000, 4) if podval('cpu_avg', pod, ns) is not None else None,
            "cpu_millicores_min": round(podval('cpu_min', pod, ns)*1000, 4) if podval('cpu_min', pod, ns) is not None else None,
            "cpu_millicores_max": round(podval('cpu_max', pod, ns)*1000, 4) if podval('cpu_max', pod, ns) is not None else None,
            "memory_MB_avg": round(podval('mem_avg', pod, ns) / (1024 * 1024), 3) if podval('mem_avg', pod, ns) is not None else None,
            "memory_MB_min": round(podval('mem_min', pod, ns) / (1024 * 1024), 3) if podval('mem_min', pod, ns) is not None else None,
            "memory_MB_max": round(podval('mem_max', pod, ns) / (1024 * 1024), 3) if podval('mem_max', pod, ns) is not None else None,
        }

    # --- NODE USAGE via direct per-node (instance=hostname) queries ---
    import time
    all_otel_nodes = set(node for ns in used_nodes_per_ns.values() for node in ns)
    node_usage = {}

    def per_node_prometheus_stats(prometheus_url, node, interval="[24h:5m]"):
        # 1. CPU %
        cpu_avg_query = f'''
            avg_over_time(
                (
                    sum without (mode)
                    (
                        avg without (cpu)
                        (
                            rate(node_cpu_seconds_total{{instance="{node}", job=~"integrations/(node_exporter|unix)", mode!="idle"}}[2m])
                        )
                    ) * 100
                ){interval}
            )
        '''
        cpu_min_query = cpu_avg_query.replace("avg_over_time", "min_over_time")
        cpu_max_query = cpu_avg_query.replace("avg_over_time", "max_over_time")

        # 2. Memory %
        mem_avg_query = f'''
            avg_over_time(
                (
                    100 -
                    (
                        avg by (instance) (
                            node_memory_MemAvailable_bytes{{instance="{node}", job=~"integrations/(node_exporter|unix)"}}
                        )
                        /
                        avg by (instance) (
                            node_memory_MemTotal_bytes{{instance="{node}", job=~"integrations/(node_exporter|unix)"}}
                        )
                        * 100
                    )
                ){interval}
            )
        '''
        mem_min_query = mem_avg_query.replace("avg_over_time", "min_over_time")
        mem_max_query = mem_avg_query.replace("avg_over_time", "max_over_time")

        def get_first_result(query):
            data = query_prometheus(prometheus_url, query)
            try:
                if data and 'result' in data['data'] and data['data']['result']:
                    return float(data['data']['result'][0]['value'][1])
            except Exception as e:
                logger.error(f"Error getting metric for node {node}: {e}")
            return None

        return {
            "cpu_percent_avg": get_first_result(cpu_avg_query),
            "cpu_percent_min": get_first_result(cpu_min_query),
            "cpu_percent_max": get_first_result(cpu_max_query),
            "memory_percent_avg": get_first_result(mem_avg_query),
            "memory_percent_min": get_first_result(mem_min_query),
            "memory_percent_max": get_first_result(mem_max_query)
        }

    for node in all_otel_nodes:
        stats = per_node_prometheus_stats(prometheus_url, node)
        # Optionally round for more readable numbers
        node_usage[node] = {
            k: round(v, 3) if v is not None else None
            for k, v in stats.items()
        }

    otel_node_count = {ns: len(nodes) for ns, nodes in used_nodes_per_ns.items()}
    total_otel_node_count = len(all_otel_nodes)
    out = {
        "otel_node_count_per_namespace": otel_node_count,
        "otel_node_count_total": total_otel_node_count,
        "pods": pod_usage,
        "nodes": node_usage,
    }

    current_time_ns = str(int(time.time() * 1e9))
    send_to_loki(
        "otel_resource_usage",
        "prometheus",
        "otel_combined_usage_24h",
        [[current_time_ns, json.dumps(out)]],
    )
    logger.info(
        f"Sent usage: {sum(len(pods) for pods in pod_usage.values())} pods, "
        f"{len(node_usage)} nodes, {total_otel_node_count} unique nodes"
    )

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
    # collect_and_send_version_data(prometheus_url, labels)
    # collect_and_send_grafana_usage(prometheus_url, labels)
    collect_and_send_otel_pod_node_usage(prometheus_url, labels)
    
    logger.info("Completed monitoring data collection")


if __name__ == "__main__":
    main() 
