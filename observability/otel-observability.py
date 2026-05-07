#!/usr/bin/env python3
"""
OpenTelemetry Observability Script

This script collects monitoring data from Prometheus and sends it to Loki.
It extracts information about monitoring components, their images, and Grafana usage.
"""

import json
import logging
import os
import re
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

_REQUEST_TIMEOUT = 30   # seconds for all HTTP calls
_LOKI_CHUNK_SIZE = 500  # max log lines per Loki push



def query_prometheus(prometheus_url: str, query: str, username: Optional[str] = None, password: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Query Prometheus and return the response.

    Args:
        prometheus_url: URL of the Prometheus instance
        query: PromQL query to execute
        username: Optional basic auth username (for multi-tenant Mimir)
        password: Optional basic auth password (for multi-tenant Mimir)

    Returns:
        JSON response from Prometheus or None if the query fails
    """
    try:
        logger.info(f"Querying Prometheus with query: {query}")
        auth = (username, password) if username and password else None
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
    data_type: str,
    values: List[List[str]]
) -> None:
    """
    Send data to Loki in JSON format with stream labels.

    Args:
        job: Job name for the stream
        source: Source of the data
        data_type: Type of data
        values: List of [timestamp, value] pairs to send
    """
    logger.info(f"Sending {data_type} data to Loki ({len(values)} entries)")

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
        "type": data_type,
        "cluster": cluster,
        "namespace": namespace,
        "customer": customer,
        "environment": environment,
        "duplo_url": duplo_url,
        "job_version": job_version
    }

    auth = (loki_username, loki_password) if loki_username and loki_password else None
    if auth:
        logger.debug("Using basic authentication for Loki")

    headers = {'Content-Type': 'application/json'}

    # Send in chunks to avoid oversized payloads
    for chunk_start in range(0, len(values), _LOKI_CHUNK_SIZE):
        chunk = values[chunk_start:chunk_start + _LOKI_CHUNK_SIZE]
        payload = {
            "streams": [
                {
                    "stream": stream,
                    "values": chunk
                }
            ]
        }
        logger.debug(f"Loki payload chunk [{chunk_start}:{chunk_start + len(chunk)}]: {json.dumps(payload, indent=2)}")
        try:
            response = requests.post(
                f"{loki_url}/loki/api/v1/push",
                json=payload,
                headers=headers,
                auth=auth,
                timeout=_REQUEST_TIMEOUT
            )
            response.raise_for_status()
            logger.info(f"Successfully sent chunk of {len(chunk)} {data_type} entries to Loki")
            logger.debug(f"Loki response status code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending data to Loki: {e}")


def format_and_send_image_data(images: Dict[str, Dict[str, Dict[str, Dict[str, str]]]], helm_versions: Optional[Dict[str, str]] = None) -> None:
    """
    Format and send image data to Loki.

    Args:
        images: Dictionary containing image information for different services
        helm_versions: Optional dict of chart name -> chart_version from HelmReleases
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

            # Process monitoring images — inject k8s-monitoring chart version if available
            if categories['monitoring']:
                monitoring_spec = dict(categories['monitoring'])
                if helm_versions and 'k8s-monitoring' in helm_versions:
                    monitoring_spec['k8s-monitoring'] = helm_versions['k8s-monitoring']
                values = [
                    [str(current_time), json.dumps({
                        "metadata": {
                            "cluster": cluster,
                            "namespace": namespace
                        },
                        "spec": monitoring_spec
                    })]
                ]
                send_to_loki(
                    "monitoring_images",
                    "prometheus",
                    "monitoring",
                    values
                )


def format_and_send_grafana_usage_data(grafana_usage: Dict[str, int]) -> None:
    """
    Format and send Grafana usage data to Loki.

    Args:
        grafana_usage: Dictionary containing Grafana datasource usage information
    """
    if not grafana_usage:
        logger.info("No Grafana usage data to send")
        return

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


def collect_image_versions(prometheus_url: str, labels: Dict[str, str], credentials: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Dict[str, Dict[str, Dict[str, str]]]]]:
    """
    Collect image versions for monitoring components from Prometheus.

    Args:
        prometheus_url: URL of the Prometheus instance
        labels: Dictionary containing additional labels
        credentials: Optional dict with 'username' and 'password' for multi-tenant Mimir

    Returns:
        Dictionary with image information categorized by cluster and namespace, then by 'main' and 'monitoring',
        or None if collection fails
    """
    logger.info("Collecting image versions from Prometheus")

    namespace_filter = labels.get('namespace_filter', '.*otel.*')
    username = credentials.get('username') if credentials else None
    password = credentials.get('password') if credentials else None

    query = f'''
    count by(cluster, namespace, container, image, pod) (
      kube_pod_container_info{{namespace=~"{namespace_filter}", container!~"config-reloader|loki-sc-rules|memcached|gateway|exporter|kube-rbac-proxy|nginx|pushgateway|duplo-observability|otel-collector"}}
    )
    '''

    prometheus_response = query_prometheus(prometheus_url, query, username, password)
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


def collect_grafana_usage(prometheus_url: str, credentials: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    """
    Collect Grafana datasource usage information from Prometheus.

    Args:
        prometheus_url: URL of the Prometheus instance
        credentials: Optional dict with 'username' and 'password' for multi-tenant Mimir

    Returns:
        Dictionary with datasource names as keys and usage counts as values
    """
    logger.info("Collecting Grafana usage data")

    username = credentials.get('username') if credentials else None
    password = credentials.get('password') if credentials else None

    query = "sum by (datasource) (clamp_min(sum_over_time(clamp_min(increase(grafana_datasource_request_total[1h]), 0)[24h:1h]) - 24 * min_over_time(clamp_min(increase(grafana_datasource_request_total[1h]), 0)[24h:1h]), 0))"

    data = query_prometheus(prometheus_url, query, username, password)
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


def collect_and_send_version_data(prometheus_url: str, labels: Dict[str, str], credentials: Optional[Dict[str, str]] = None) -> None:
    """
    Collect image versions from Prometheus and send them to Loki.
    """
    logger.info("Collecting and sending image data")
    images = collect_image_versions(prometheus_url, labels, credentials)
    if not images:
        logger.error("Failed to collect image versions")
        return
    format_and_send_image_data(images, labels)
    logger.info("Completed image data collection and sending")


def collect_and_send_grafana_usage(prometheus_url: str, labels: Dict[str, str], credentials: Optional[Dict[str, str]] = None) -> None:
    """
    Collect Grafana usage data from Prometheus and send it to Loki.
    """
    logger.info("Collecting and sending Grafana usage data")
    grafana_usage = collect_grafana_usage(prometheus_url, credentials)
    format_and_send_grafana_usage_data(grafana_usage, labels)
    logger.info("Completed Grafana usage data collection and sending")

def query_loki(loki_url: str, query: str, username: Optional[str] = None, password: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Run a LogQL instant metric query against Loki and return the response.
    """
    try:
        logger.info(f"Querying Loki with query: {query}")
        auth = (username, password) if username and password else None
        response = requests.get(
            f"{loki_url}/loki/api/v1/query",
            params={'query': query},
            auth=auth
        )
        response.raise_for_status()
        logger.debug("Successfully received response from Loki")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Loki: {e}")
        return None


def collect_and_send_grafana_db_lock_errors(labels: Dict[str, str], credentials: Optional[Dict[str, str]] = None) -> None:
    """
    Count 'database is locked' errors in grafana-ui logs over the last 24h and send to central Loki.
    """
    logger.info("Collecting and sending Grafana DB lock error data")

    namespace = labels.get('namespace') or os.getenv('NAMESPACE', '')
    service = 'grafana-ui'
    username = credentials.get('username') if credentials else None
    password = credentials.get('password') if credentials else None

    source_loki_url = os.getenv('SOURCE_LOKI_URL') or f"http://duplo-logging-gateway.{namespace}.svc.cluster.local"
    query = f'sum(count_over_time({{namespace="{namespace}", service_name="{service}"}} |= `database is locked` != `logger=tsdb` [24h]))'

    data = query_loki(source_loki_url, query, username, password)

    count = 0
    if data and 'data' in data and 'result' in data['data'] and data['data']['result']:
        try:
            count = int(float(data['data']['result'][0]['value'][1]))
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"Error parsing Loki DB lock count: {e}")

    logger.info(f"Grafana DB lock error count (last 24h): {count}")

    current_time = str(int(time.time() * 1_000_000_000))
    values = [[current_time, json.dumps({
        "namespace": namespace,
        "service": service,
        "db_lock_error_count_24h": count
    })]]

    send_to_loki(
        "grafana_db_lock_errors",
        "loki",
        "db_lock_error_count_24h",
        values
    )
    logger.info("Completed Grafana DB lock error data collection and sending")


def collect_and_send_otel_pod_node_usage(prometheus_url: str, labels: dict, credentials: Optional[Dict[str, str]] = None) -> None:
    """
    Collects 24h pod/node resource stats and otel_node_count for all clusters/namespaces;
    sends them in a *single* Loki push (chunked), one log-line per resource, in your requested JSON format.
    """
    logger.info("Collecting 24h OTEL pod/node usage statistics")
    namespace_regex = labels.get('namespace_filter', '.*otel.*')
    username = credentials.get('username') if credentials else None
    password = credentials.get('password') if credentials else None

    # 1. Excluded pods (DaemonSet/Job)
    def excluded_pods_by_owner_kind(kind):
        query = f'kube_pod_owner{{namespace=~"{namespace_regex}",owner_kind="{kind}"}}'
        response = query_prometheus(prometheus_url, query, username, password) or {}
        return {(m['metric'].get('cluster'), m['metric'].get('namespace'), m['metric'].get('pod'))
                for m in response.get("data", {}).get("result", [])}

    daemonset_pods = excluded_pods_by_owner_kind("DaemonSet")
    job_pods = excluded_pods_by_owner_kind("Job")
    excluded_pods = daemonset_pods | job_pods

    # 2. Pod-to-node mapping
    pod_to_node = {}
    pod_node_query = f'kube_pod_info{{namespace=~"{namespace_regex}"}}'
    pod_node_response = query_prometheus(prometheus_url, pod_node_query, username, password) or {}
    for record in pod_node_response.get("data", {}).get("result", []):
        cluster = record['metric'].get('cluster')
        namespace = record['metric'].get('namespace')
        pod_name = record['metric'].get('pod')
        node_name = record['metric'].get('node')
        if cluster and namespace and pod_name and node_name:
            pod_to_node[(cluster, namespace, pod_name)] = node_name

    # 3. Node -> instance_type mapping
    instance_type_query = 'kube_node_labels{job="integrations/kubernetes/kube-state-metrics"}'
    instance_type_data = query_prometheus(prometheus_url, instance_type_query, username, password) or {}
    node_instance_type = {}
    for res in instance_type_data.get("data", {}).get("result", []):
        cluster = res['metric'].get('cluster')
        node_name = res['metric'].get('label_kubernetes_io_hostname')
        instance_type = (
            res['metric'].get('label_node_kubernetes_io_instance_type') or
            res['metric'].get('label_beta_kubernetes_io_instance_type') or
            None
        )
        if cluster and node_name and instance_type:
            node_instance_type[(cluster, node_name)] = instance_type

    # 4. Pod resource requests/limits
    def extract_pod_resource_usage(prometheus_query):
        response = query_prometheus(prometheus_url, prometheus_query, username, password)
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

    # 5. Pod 24h usage — query all stats up front, then build O(1) lookup dicts
    label_filter = f'namespace=~"{namespace_regex}",container!="",container!="POD"'
    promql_templates = {
        "cpu_avg": f'avg by (pod,namespace,cluster) (avg_over_time(rate(container_cpu_usage_seconds_total{{{label_filter}}}[5m])[24h:5m]))',
        "cpu_min": f'min by (pod,namespace,cluster) (min_over_time(rate(container_cpu_usage_seconds_total{{{label_filter}}}[5m])[24h:5m]))',
        "cpu_max": f'max by (pod,namespace,cluster) (max_over_time(rate(container_cpu_usage_seconds_total{{{label_filter}}}[5m])[24h:5m]))',
        "mem_avg": f'avg by (pod,namespace,cluster) (avg_over_time(container_memory_rss{{{label_filter}}}[24h]))',
        "mem_min": f'min by (pod,namespace,cluster) (min_over_time(container_memory_rss{{{label_filter}}}[24h]))',
        "mem_max": f'max by (pod,namespace,cluster) (max_over_time(container_memory_rss{{{label_filter}}}[24h]))',
    }
    pod_usage_stats = {k: query_prometheus(prometheus_url, query, username, password) for k, query in promql_templates.items()}

    # Build O(1) lookup dicts (replaces the O(n²) linear scan)
    stat_lookup = _build_stat_lookup(pod_usage_stats)

    def usage_stat(stat: str, cluster: str, pod_name: str, namespace: str) -> Optional[float]:
        return stat_lookup[stat].get((cluster, namespace, pod_name))

    pods_by_namespace: Dict[Tuple[str, str], list] = {}
    nodes_by_namespace: Dict[Tuple[str, str], set] = {}

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

        # Fetch each stat once and reuse the value
        cpu_avg_v = usage_stat('cpu_avg', cluster, pod_name, namespace)
        cpu_min_v = usage_stat('cpu_min', cluster, pod_name, namespace)
        cpu_max_v = usage_stat('cpu_max', cluster, pod_name, namespace)
        mem_avg_v = usage_stat('mem_avg', cluster, pod_name, namespace)
        mem_min_v = usage_stat('mem_min', cluster, pod_name, namespace)
        mem_max_v = usage_stat('mem_max', cluster, pod_name, namespace)
        cpu_req_v = cpu_request.get((cluster, namespace, pod_name))
        cpu_lim_v = cpu_limit.get((cluster, namespace, pod_name))
        mem_req_v = mem_request.get((cluster, namespace, pod_name))
        mem_lim_v = mem_limit.get((cluster, namespace, pod_name))

        pod_info = {
            "pod": pod_name,
            "node": node_name,
            "cpu_millicores_avg":     round(cpu_avg_v * 1000, 4) if cpu_avg_v is not None else None,
            "cpu_millicores_min":     round(cpu_min_v * 1000, 4) if cpu_min_v is not None else None,
            "cpu_millicores_max":     round(cpu_max_v * 1000, 4) if cpu_max_v is not None else None,
            "memory_MB_avg":          round(mem_avg_v / (1024 * 1024), 3) if mem_avg_v is not None else None,
            "memory_MB_min":          round(mem_min_v / (1024 * 1024), 3) if mem_min_v is not None else None,
            "memory_MB_max":          round(mem_max_v / (1024 * 1024), 3) if mem_max_v is not None else None,
            "cpu_millicores_request": round(cpu_req_v * 1000, 2) if cpu_req_v is not None else None,
            "cpu_millicores_limit":   round(cpu_lim_v * 1000, 2) if cpu_lim_v is not None else None,
            "memory_MB_request":      round(mem_req_v / (1024 * 1024), 2) if mem_req_v is not None else None,
            "memory_MB_limit":        round(mem_lim_v / (1024 * 1024), 2) if mem_lim_v is not None else None,
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
            data = query_prometheus(prometheus_url, query, username, password)
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
    values = []
    sent_count = 0
    for cluster, namespace in all_ns_keys:
        pods = pods_by_namespace.get((cluster, namespace), [])
        node_names = sorted({pod["node"] for pod in pods})

        # Log node count entry
        entry_node_count = {
            "metadata": {
                "cluster": cluster,
                "namespace": namespace,
            },
            "spec": {
                "otel_node_count": len(node_names)
            }
        }
        values.append([current_time_ns, json.dumps(entry_node_count)])
        sent_count += 1

        # Log individual node entries
        for node_name in node_names:
            stats = all_node_stats.get((cluster, node_name), {})
            node_info = {
                "metadata": {
                    "cluster": cluster,
                    "namespace": namespace,
                },
                "spec": {
                    "node": node_name,
                    "instance_type": node_instance_type.get((cluster, node_name)),
                }
            }
            node_info["spec"].update({k: round(v, 3) if v is not None else None for k, v in stats.items()})
            values.append([current_time_ns, json.dumps(node_info)])
            sent_count += 1

        # Log individual pod entries
        for pod in pods:
            pod_info = {
                "metadata": {
                    "cluster": cluster,
                    "namespace": namespace,
                },
                "spec": dict(pod)
            }
            values.append([current_time_ns, json.dumps(pod_info)])
            sent_count += 1

    if values:
        send_to_loki(
            "otel_resource_usage",
            "prometheus",
            "otel_combined_usage_24h_per_ns",
            values
        )
        logger.info(f"Sent {sent_count} Loki log entries")
    else:
        logger.warning("No Loki messages were sent! All data may have been empty or filtered out.")



def collect_helm_chart_versions(namespace: str) -> List[Dict[str, Any]]:
    """
    Collect Helm chart versions from pod labels via the Kubernetes API.

    Queries pods with the 'helm.sh/chart' label and extracts chart name
    and version. Deduplicates by release name so only one record per
    Helm release is returned. Does not require Flux, CRDs, or secret access.

    Returns:
        List of dicts with chart version info per Helm release.
    """
    logger.info("Collecting Helm chart versions from pod labels")
    records = []

    token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ca_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')

    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError as e:
        logger.error(f"Could not read service account token: {e}")
        return records

    url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/pods"
    headers = {'Authorization': f'Bearer {token}'}
    params = {'labelSelector': 'helm.sh/chart'}

    try:
        response = requests.get(url, headers=headers, verify=ca_path, timeout=_REQUEST_TIMEOUT, params=params)
        response.raise_for_status()
        items = response.json().get('items', [])
        seen_releases = set()
        for pod in items:
            labels = pod.get('metadata', {}).get('labels', {})
            chart_label = labels.get('helm.sh/chart', '')
            release_name = labels.get('app.kubernetes.io/instance', '')
            if not chart_label or release_name in seen_releases:
                continue
            # chart_label format: "chartname-1.2.3"
            match = re.match(r'^(.+)-(\d+\..+)$', chart_label)
            if match:
                chart_name, chart_version = match.group(1), match.group(2)
            else:
                chart_name, chart_version = chart_label, None
            seen_releases.add(release_name)
            records.append({
                "metadata": {"cluster": os.getenv('CLUSTER', ''), "namespace": namespace},
                "spec": {
                    "release": release_name,
                    "chart": chart_name,
                    "chart_version": chart_version,
                    "ready": "True"
                }
            })
        logger.info(f"Collected {len(records)} Helm chart version records")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for pod labels: {e}")

    return records


def collect_and_send_helm_chart_versions(namespace: str) -> None:
    """Collect Helm chart versions and send to Loki."""
    logger.info("Collecting and sending Helm chart versions")
    records = collect_helm_chart_versions(namespace)
    if not records:
        logger.warning("No Helm chart version data collected")
        return

    current_time_ns = str(int(time.time() * 1e9))
    values = [[current_time_ns, json.dumps(r)] for r in records]
    send_to_loki("helm_chart_versions", "kubernetes", "helm_chart_version_info", values)
    logger.info("Completed Helm chart version collection and sending")



def main() -> None:
    """
    Main function that orchestrates the monitoring data collection process.

    This function:
    1. Retrieves configuration from environment variables
    2. Validates required environment variables
    3. Collects and sends image data
    4. Collects and sends Grafana usage data
    5. Collects and sends Helm chart versions
    """
    logger.info("Starting monitoring data collection")

    # Validate environment variables
    is_valid, labels, _ = validate_environment_variables()
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
