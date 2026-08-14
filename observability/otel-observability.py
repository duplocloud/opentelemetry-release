#!/usr/bin/env python3
"""
OpenTelemetry Observability Script

This script collects monitoring data from Prometheus and sends it to Loki.
It extracts information about monitoring components, their images, and Grafana usage.
"""

import base64
import gzip
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

REQUIRED_CUSTOMER_PATHS = {
    'duplo-metrics': {
        'aws':   ['serviceAccount.name', 'ruler.serviceAccount.name',
                  'mimir.structuredConfig.common.storage.s3.endpoint',
                  'mimir.structuredConfig.common.storage.s3.bucket_name'],
        'azure': ['serviceAccount.name', 'ruler.serviceAccount.name',
                  'mimir.structuredConfig.common.storage.azure.account_name'],
        'gcp':   ['serviceAccount.name', 'ruler.serviceAccount.name',
                  'mimir.structuredConfig.common.storage.gcs.bucket_name'],
    },
    'duplo-logging': {
        'aws':   ['serviceAccount.name',
                  'loki.storage.bucketNames.chunks',
                  'loki.storage.s3.region'],
        'azure': ['serviceAccount.name',
                  'loki.storage.azure.accountName'],
        'gcp':   ['serviceAccount.name',
                  'loki.storage.bucketNames.chunks'],
    },
    'duplo-tracing': {
        'aws':   ['serviceAccount.name',
                  'storage.trace.s3.bucket',
                  'metricsGenerator.config.storage.remote_write'],
        'azure': ['serviceAccount.name',
                  'storage.trace.azure.storage_account_name',
                  'metricsGenerator.config.storage.remote_write'],
        'gcp':   ['serviceAccount.name',
                  'storage.trace.gcs.bucket_name',
                  'metricsGenerator.config.storage.remote_write'],
    },
}


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
            params={'query': query},
            auth=auth
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
      kube_pod_container_info{{namespace=~"{namespace_filter}", container!~"config-reloader|loki-sc-rules|memcached|gateway|exporter|kube-rbac-proxy|nginx|pushgateway"}}
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


def collect_pod_annotations() -> Dict[tuple, Dict[str, Optional[str]]]:
    """
    Collect safe-to-evict and memory-limit-oom-score-adj annotations by reading pod metadata
    directly from the Kubernetes API.

    Always queries the NAMESPACE env var directly — that is always set to the namespace where
    otel components are deployed, regardless of whether the namespace name matches the
    namespace_filter regex (e.g. duploservices-aos vs duploservices-otel-o11y).
    """
    token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ca_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')
    cluster = os.getenv('CLUSTER', '')
    namespace = os.getenv('NAMESPACE', '')

    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError as e:
        logger.error(f"Could not read service account token: {e}")
        return {}

    if not namespace:
        logger.warning("NAMESPACE env var not set; cannot collect pod annotations")
        return {}

    annotations_map: Dict[tuple, Dict[str, Optional[str]]] = {}

    try:
        url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/pods"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, verify=ca_path)
        response.raise_for_status()
        for pod in response.json().get('items', []):
            metadata = pod.get('metadata', {})
            pod_name = metadata.get('name', '')
            annotations = metadata.get('annotations') or {}
            annotations_map[(cluster, namespace, pod_name)] = {
                "safe_to_evict": annotations.get('cluster-autoscaler.kubernetes.io/safe-to-evict'),
                "memory_limit_oom_score_adj": annotations.get('container.kubernetes.io/memory-limit-oom-score-adj'),
            }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for pod annotations: {e}")

    logger.info(f"Collected annotations for {len(annotations_map)} pods")
    return annotations_map


ANNOTATION_POD_COMPONENTS = re.compile(
    r'(ingester|metrics-generator|metricsgenerator|write|backend|read|querier)',
    re.IGNORECASE
)


def collect_and_send_pod_annotations(labels: Dict[str, str]) -> None:
    """
    Collect safe-to-evict and memory-limit-oom-score-adj annotations for otel
    components (ingester, metrics-generator, write, backend, read, querier).
    Deduplicates to one entry per component type per namespace — handles both
    StatefulSet and Deployment pods without emitting duplicate entries per replica.
    Runs independently of Prometheus availability.
    """
    logger.info("Collecting and sending pod annotations")
    annotations_map = collect_pod_annotations()
    if not annotations_map:
        logger.warning("No pod annotations collected")
        return

    current_time_ns = str(int(time.time() * 1_000_000_000))
    seen_components: set = set()
    values = []
    for (cluster, namespace, pod_name), ann in annotations_map.items():
        m = ANNOTATION_POD_COMPONENTS.search(pod_name)
        if not m:
            continue
        component = m.group(1).lower()
        # Everything before the component keyword is the release prefix
        # e.g. "duplo-tracing-ingester-0" → product="duplo-tracing"
        product = pod_name[:m.start()].rstrip('-')
        dedup_key = (cluster, namespace, product, component)
        if dedup_key in seen_components:
            continue
        seen_components.add(dedup_key)
        values.append([current_time_ns, json.dumps({
            "metadata": {"cluster": cluster, "namespace": namespace},
            "spec": {
                "product": product,
                "component": component,
                "safe_to_evict": ann.get("safe_to_evict"),
                "memory_limit_oom_score_adj": ann.get("memory_limit_oom_score_adj"),
            }
        })])

    if not values:
        logger.warning("No matching component pods found for annotation collection")
        return

    send_to_loki("pod_annotations", "kubernetes", "pod_annotation_info", values)
    logger.info(f"Sent pod annotations for {len(values)} pods")


def collect_and_send_otel_pod_node_usage(prometheus_url: str, labels: dict, credentials: Optional[Dict[str, str]] = None) -> None:
    """
    Collects 24h pod/node resource stats and otel_node_count for all clusters/namespaces;
    sends them in a *single* Loki push, one log-line per resource, in your requested JSON format.
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

    # 2. Pod annotations (safe-to-evict, memory-limit-oom-score-adj) from K8s pod metadata
    pod_annotations = collect_pod_annotations()

    # 3. Pod-to-node mapping
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

    # 4. Node -> instance_type mapping
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

    # 5. Pod resource requests/limits
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

    # 6. Pod 24h usage
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
        ann = pod_annotations.get((cluster, namespace, pod_name), {})
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
            "safe_to_evict": ann.get("safe_to_evict"),
            "memory_limit_oom_score_adj": ann.get("memory_limit_oom_score_adj"),
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
        node_names = sorted(list({pod["node"] for pod in pods}))

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
            stats = node_resource_stats(prometheus_url, node_name, cluster)
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
        logger.info(f"Sent {sent_count} Loki log entries in one POST")
    else:
        logger.warning("No Loki messages were sent! All data may have been empty or filtered out.")


def collect_helm_chart_versions(namespace: str) -> List[Dict[str, Any]]:
    """
    Collect Helm chart versions from Helm release secrets (owner=helm,status=deployed).
    Secrets are the authoritative source for chart name and version for all releases,
    including umbrella charts.
    """
    logger.info("Collecting Helm chart versions")
    # Maps release_name -> (revision, record) to keep only the highest revision
    best_records: Dict[str, Any] = {}

    token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ca_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')

    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError as e:
        logger.error(f"Could not read service account token: {e}")
        return []

    try:
        url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/secrets"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, verify=ca_path,
                                params={'labelSelector': 'owner=helm,status=deployed'})
        response.raise_for_status()
        for secret in response.json().get('items', []):
            release_name = secret.get('metadata', {}).get('labels', {}).get('name', '')
            if not release_name:
                continue
            release_b64 = secret.get('data', {}).get('release')
            if not release_b64:
                continue

            # Extract revision from secret name: sh.helm.release.v1.<release>.v<N>
            secret_name = secret.get('metadata', {}).get('name', '')
            try:
                revision = int(secret_name.rsplit('.v', 1)[-1])
            except (ValueError, IndexError):
                revision = 0

            # Skip if we already have a higher revision for this release
            if release_name in best_records and best_records[release_name][0] >= revision:
                continue

            try:
                # K8s base64-encodes secret data; Helm also base64+gzip-encodes the release.
                # So the value is double-encoded: base64(base64(gzip(json))).
                helm_encoded = base64.b64decode(release_b64)
                release_data = json.loads(gzip.decompress(base64.b64decode(helm_encoded)).decode('utf-8'))
                chart_name = release_data.get('chart', {}).get('metadata', {}).get('name', release_name)
                chart_version = release_data.get('chart', {}).get('metadata', {}).get('version')
                subcharts = [
                    (dep.get('name'), dep.get('version'))
                    for dep in release_data.get('chart', {}).get('metadata', {}).get('dependencies', [])
                    if dep.get('name')
                ]
            except Exception as e:
                logger.warning(f"Could not decode Helm secret for release {release_name}: {e}")
                continue

            best_records[release_name] = (revision, {
                "metadata": {"cluster": os.getenv('CLUSTER', ''), "namespace": namespace},
                "spec": {
                    "release": release_name,
                    "chart": chart_name,
                    "chart_version": chart_version,
                    "ready": "True"
                }
            }, subcharts)
            logger.debug(f"Secret: release '{release_name}' revision {revision}, chart '{chart_name}' v{chart_version}, subcharts: {len(subcharts)}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for Helm secrets: {e}")
        raise

    records = []
    for revision, parent_record, subcharts in best_records.values():
        records.append(parent_record)
        parent_chart_name = parent_record['spec']['chart']
        for dep_name, dep_version in subcharts:
            records.append({
                "metadata": dict(parent_record['metadata']),
                "spec": {
                    "release": parent_record['spec']['release'],
                    "chart": dep_name,
                    "chart_version": dep_version,
                    "parent_chart": parent_chart_name,
                    "ready": "True"
                }
            })
    logger.info(f"Total Helm chart version records: {len(records)}")
    return records


def _get_nested(d: Any, *keys: str, default: Any = None) -> Any:
    """Walk nested dicts by key sequence; return default if any key is missing or not a dict."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)
        if d is default:
            return default
    return d


def collect_and_send_helm_chart_versions(namespace: str) -> None:
    """Collect Helm chart versions from Helm release secrets and send to Loki."""
    logger.info("Collecting and sending Helm chart versions")
    try:
        records = collect_helm_chart_versions(namespace)
    except requests.exceptions.RequestException as e:
        current_time_ns = str(int(time.time() * 1e9))
        error_record = {
            "metadata": {"cluster": os.getenv('CLUSTER', ''), "namespace": namespace},
            "spec": {"error": str(e)}
        }
        send_to_loki("helm_chart_versions", "kubernetes", "helm_chart_version_info", [[current_time_ns, json.dumps(error_record)]])
        return
    if not records:
        logger.warning("No Helm chart version data collected")
        return
    current_time_ns = str(int(time.time() * 1e9))
    values = [[current_time_ns, json.dumps(r)] for r in records]
    send_to_loki("helm_chart_versions", "kubernetes", "helm_chart_version_info", values)
    logger.info("Completed Helm chart version collection and sending")


def collect_helm_config_values(namespace: str) -> List[Dict[str, Any]]:
    """
    Read Helm release secrets and extract from user-supplied values:
      - safe-to-evict pod annotation
      - memory-limit-oom-score-adj pod annotation
      - ingester replication factor (Mimir chart)
    Checks global and per-component podAnnotations; user values take priority over chart defaults.
    """
    token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ca_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')

    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError as e:
        logger.error(f"Could not read service account token: {e}")
        return []

    best_revisions: Dict[str, int] = {}
    best_configs: Dict[str, Dict[str, Any]] = {}

    try:
        url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/secrets"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, verify=ca_path,
                                params={'labelSelector': 'owner=helm,status=deployed'})
        response.raise_for_status()
        for secret in response.json().get('items', []):
            release_name = secret.get('metadata', {}).get('labels', {}).get('name', '')
            if not release_name:
                continue
            release_b64 = secret.get('data', {}).get('release')
            if not release_b64:
                continue
            secret_name = secret.get('metadata', {}).get('name', '')
            try:
                revision = int(secret_name.rsplit('.v', 1)[-1])
            except (ValueError, IndexError):
                revision = 0
            if release_name in best_revisions and best_revisions[release_name] >= revision:
                continue
            try:
                helm_encoded = base64.b64decode(release_b64)
                release_data = json.loads(gzip.decompress(base64.b64decode(helm_encoded)).decode('utf-8'))
            except Exception as e:
                logger.warning(f"Could not decode Helm secret for release '{release_name}': {e}")
                continue
            best_revisions[release_name] = revision
            best_configs[release_name] = {
                'chart_name': release_data.get('chart', {}).get('metadata', {}).get('name', release_name),
                'user_values': release_data.get('config') or {},
                'chart_defaults': release_data.get('chart', {}).get('values') or {},
            }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for Helm config values: {e}")
        return []

    records = []
    cluster = os.getenv('CLUSTER', '')

    for release_name, cfg in best_configs.items():
        user_vals = cfg['user_values']
        defaults = cfg['chart_defaults']

        def merged(*keys: str) -> Any:
            v = _get_nested(user_vals, *keys)
            return v if v is not None else _get_nested(defaults, *keys)

        spec: Dict[str, Any] = {"release": release_name, "chart": cfg['chart_name']}

        # Ingester replication factor — checked in priority order:
        #   Mimir explicit:  mimir.structuredConfig.ingester.ring.replication_factor
        #   Tempo explicit:  ingester.config.replication_factor
        #   Generic ring:    ingester.ring.replicationFactor / replication_factor
        #   Fallback:        ingester.replicas (chart default; equals RF when zone-aware disabled)
        rf = (merged('mimir', 'structuredConfig', 'ingester', 'ring', 'replication_factor') or
              merged('ingester', 'config', 'replication_factor') or
              merged('ingester', 'ring', 'replicationFactor') or
              merged('ingester', 'ring', 'replication_factor') or
              merged('ingester', 'replicas'))
        if rf:
            try:
                spec['ingester_replication_factor'] = int(rf)
            except (ValueError, TypeError):
                pass

        # Only emit a record if at least one relevant field was found
        if len(spec) > 2:
            records.append({
                "metadata": {"cluster": cluster, "namespace": namespace},
                "spec": spec
            })

    logger.info(f"Collected Helm config values for {len(records)} releases")
    return records


def collect_and_send_helm_config_values(namespace: str) -> None:
    """Collect safe-to-evict, memory-limit-oom-score-adj, and ingester replication factor
    from Helm release values and send to Loki."""
    logger.info("Collecting Helm config values (pod annotations + ingester replication factor)")
    records = collect_helm_config_values(namespace)
    if not records:
        logger.warning("No Helm config values found to send")
        return
    current_time_ns = str(int(time.time() * 1e9))
    values = [[current_time_ns, json.dumps(r)] for r in records]
    send_to_loki("helm_config_values", "kubernetes", "helm_config_value_info", values)
    logger.info("Completed Helm config values collection and sending")


def collect_ingester_replication_factor(namespace: str) -> Optional[int]:
    """
    Read the Mimir ingester replication factor from a Kubernetes ConfigMap.
    ConfigMap name is controlled by the MIMIR_CONFIGMAP_NAME env var (default: mimir-config).
    Supports both a flat 'ingester-replication-factor' key and a nested YAML config file.
    """
    token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ca_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')
    configmap_name = os.getenv('MIMIR_CONFIGMAP_NAME', 'mimir-config')

    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError as e:
        logger.error(f"Could not read service account token: {e}")
        return None

    try:
        url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/configmaps/{configmap_name}"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, verify=ca_path)
        response.raise_for_status()
        cm_data = response.json().get('data', {})

        # Check flat keys first
        for key in ('ingester-replication-factor', 'replication_factor', 'replication-factor'):
            if key in cm_data:
                try:
                    return int(cm_data[key])
                except (ValueError, TypeError):
                    pass

        # Try parsing YAML config files within the ConfigMap
        try:
            import yaml
            for key, value in cm_data.items():
                if not isinstance(value, str):
                    continue
                if key.endswith(('.yaml', '.yml')) or key in ('config', 'mimir-config'):
                    try:
                        config = yaml.safe_load(value)
                        if isinstance(config, dict):
                            ring = config.get('ingester', {}).get('ring', {})
                            rf = ring.get('replication-factor') or ring.get('replication_factor')
                            if rf is not None:
                                return int(rf)
                    except yaml.YAMLError as ye:
                        logger.warning(f"Could not parse ConfigMap key '{key}' as YAML: {ye}")
        except ImportError:
            logger.warning("PyYAML not available; skipping YAML config parsing for replication factor")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for ConfigMap '{configmap_name}': {e}")

    logger.warning(f"Ingester replication factor not found in ConfigMap '{configmap_name}'")
    return None


def collect_and_send_mimir_config(namespace: str) -> None:
    """Collect Mimir ingester replication factor from ConfigMap and send to Loki."""
    logger.info("Collecting Mimir ingester replication factor from ConfigMap")
    replication_factor = collect_ingester_replication_factor(namespace)
    if replication_factor is None:
        logger.warning("Could not determine ingester replication factor; skipping send")
        return

    current_time_ns = str(int(time.time() * 1e9))
    record = {
        "metadata": {"cluster": os.getenv('CLUSTER', ''), "namespace": namespace},
        "spec": {"ingester_replication_factor": replication_factor}
    }
    send_to_loki(
        "mimir_config",
        "kubernetes",
        "ingester_config",
        [[current_time_ns, json.dumps(record)]]
    )
    logger.info(f"Sent ingester replication factor: {replication_factor}")


def get_nested(d: dict, path: str) -> Any:
    """Traverse a dict by dot-notation path. Returns None if any key is missing."""
    for key in path.split('.'):
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d


def detect_cloud(values: dict) -> str:
    """Infer cloud provider from the Helm values structure."""
    m = values.get('mimir', {}).get('structuredConfig', {}).get('common', {}).get('storage', {})
    if 's3' in m or values.get('loki', {}).get('storage', {}).get('s3') or values.get('storage', {}).get('trace', {}).get('s3'):
        return 'aws'
    if 'azure' in m or values.get('loki', {}).get('storage', {}).get('azure') or values.get('storage', {}).get('trace', {}).get('azure'):
        return 'azure'
    if 'gcs' in m or values.get('storage', {}).get('trace', {}).get('gcs'):
        return 'gcp'
    return 'unknown'


def check_customer_values(release: str, values: dict) -> dict:
    """
    Compare Helm release values against the expected customer-value paths for that chart and cloud.
    Returns cloud, required paths, missing paths, and whether values are complete.
    """
    cloud = detect_cloud(values)
    required = REQUIRED_CUSTOMER_PATHS.get(release, {}).get(cloud, [])
    missing = [p for p in required if get_nested(values, p) is None]
    return {
        'cloud': cloud,
        'required': required,
        'missing': missing,
        'match': len(missing) == 0,
    }


def _base_configmap_keys(release: str, namespace: str, token: str, k8s_host: str, k8s_port: str, ca_path: str) -> set:
    """
    Find the base-values ConfigMap for a release (pattern: *-{release}-base-values) and
    return its top-level YAML keys. Returns an empty set if no ConfigMap is found.
    """
    try:
        url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/configmaps"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, verify=ca_path)
        response.raise_for_status()
        pattern = re.compile(rf'.+-{re.escape(release)}-base-values$')
        for cm in response.json().get('items', []):
            name = cm.get('metadata', {}).get('name', '')
            if not pattern.match(name):
                continue
            raw = cm.get('data', {}).get('values.yaml', '')
            keys = {m.group(1) for m in re.finditer(r'^"?([a-zA-Z0-9_\-]+)"?\s*:', raw, re.MULTILINE)}
            logger.debug(f"Base ConfigMap '{name}' for release '{release}': {len(keys)} top-level keys")
            return keys
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not fetch base ConfigMap for release '{release}': {e}")
    return set()


def collect_and_send_customer_structure(namespace: str) -> None:
    """
    For each tracked Helm release (duplo-metrics, duplo-logging, duplo-tracing), read the
    deployed user-supplied values and check them against REQUIRED_CUSTOMER_PATHS.

    Emits one Loki record per release with:
      - structure: 'base-only'     — Helm user values match the base ConfigMap (no customer overrides)
                   'base-customer' — Helm user values contain keys beyond the base ConfigMap
      - cloud: detected cloud provider
      - match: True if all required paths are present in the merged values
      - missing: list of paths that are absent
    """
    logger.info("Collecting customer Helm value structure")

    token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ca_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')
    cluster = os.getenv('CLUSTER', '')

    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError as e:
        logger.error(f"Could not read service account token: {e}")
        return

    tracked_charts = set(REQUIRED_CUSTOMER_PATHS.keys())
    best_revisions: Dict[str, int] = {}
    best_release_data: Dict[str, Dict[str, Any]] = {}

    try:
        url = f"https://{k8s_host}:{k8s_port}/api/v1/namespaces/{namespace}/secrets"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, verify=ca_path,
                                params={'labelSelector': 'owner=helm,status=deployed'})
        response.raise_for_status()
        for secret in response.json().get('items', []):
            release_name = secret.get('metadata', {}).get('labels', {}).get('name', '')
            if not release_name or release_name not in tracked_charts:
                continue
            release_b64 = secret.get('data', {}).get('release')
            if not release_b64:
                continue
            secret_name = secret.get('metadata', {}).get('name', '')
            try:
                revision = int(secret_name.rsplit('.v', 1)[-1])
            except (ValueError, IndexError):
                revision = 0
            if release_name in best_revisions and best_revisions[release_name] >= revision:
                continue
            try:
                helm_encoded = base64.b64decode(release_b64)
                release_data = json.loads(gzip.decompress(base64.b64decode(helm_encoded)).decode('utf-8'))
            except Exception as e:
                logger.warning(f"Could not decode Helm secret for release '{release_name}': {e}")
                continue
            best_revisions[release_name] = revision
            best_release_data[release_name] = {
                'user_values': release_data.get('config') or {},
                'chart_defaults': release_data.get('chart', {}).get('values') or {},
            }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for customer structure: {e}")
        return

    results = []
    for release, data in best_release_data.items():
        user_values = data['user_values']
        merged_values = {**data['chart_defaults'], **user_values}

        # Compare top-level keys in helm user values against the base ConfigMap.
        # base-only:     no extra keys beyond ConfigMap (customer hasn't added overrides)
        # base-customer: helm user values contain keys not in the base ConfigMap
        cm_keys = _base_configmap_keys(release, namespace, token, k8s_host, k8s_port, ca_path)
        extra_keys = set(user_values.keys()) - cm_keys
        structure = 'base-customer' if extra_keys else 'base-only'

        check = check_customer_values(release, merged_values)

        results.append({
            'release': release,
            'structure': structure,
            'cloud': check['cloud'],
            'match': check['match'],
            'missing': check['missing'],
        })
        logger.debug(f"Release '{release}': structure={structure}, cloud={check['cloud']}, "
                     f"match={check['match']}, missing={check['missing']}")

    if not results:
        logger.warning("No tracked Helm releases found for customer structure check")
        return

    current_time_ns = str(int(time.time() * 1e9))
    values = [[current_time_ns, json.dumps({
        "metadata": {"cluster": cluster, "namespace": namespace},
        "spec": r
    })] for r in results]
    send_to_loki("customer_structure", "kubernetes", "helm_customer_structure", values)
    logger.info(f"Sent customer structure for {len(results)} releases")


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
    prom_user = os.getenv('SOURCE_PROMETHEUS_USERNAME', '').strip()
    prom_pass = os.getenv('SOURCE_PROMETHEUS_PASSWORD', '').strip()
    prometheus_creds = {'username': prom_user, 'password': prom_pass} if prom_user and prom_pass else None

    loki_user = os.getenv('SOURCE_LOKI_USERNAME', '').strip()
    loki_pass = os.getenv('SOURCE_LOKI_PASSWORD', '').strip()
    loki_creds = {'username': loki_user, 'password': loki_pass} if loki_user and loki_pass else None

    # Collect and send data
    collect_and_send_version_data(prometheus_url, labels, prometheus_creds)
    collect_and_send_grafana_usage(prometheus_url, labels, prometheus_creds)
    collect_and_send_otel_pod_node_usage(prometheus_url, labels, prometheus_creds)
    collect_and_send_grafana_db_lock_errors(labels, loki_creds)
    collect_and_send_pod_annotations(labels)
    collect_and_send_helm_chart_versions(os.getenv('NAMESPACE', ''))
    collect_and_send_helm_config_values(os.getenv('NAMESPACE', ''))
    collect_and_send_mimir_config(os.getenv('NAMESPACE', ''))
    collect_and_send_customer_structure(os.getenv('NAMESPACE', ''))

    logger.info("Completed monitoring data collection")


if __name__ == "__main__":
    main() 
