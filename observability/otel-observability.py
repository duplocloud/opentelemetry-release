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

_REQUEST_TIMEOUT = 30   # seconds for all HTTP calls
_LOKI_CHUNK_SIZE = 500  # max log lines per Loki push


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
            params={'query': query},
            timeout=_REQUEST_TIMEOUT
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
    query = "sum by (datasource) (clamp_min(sum_over_time(clamp_min(increase(grafana_datasource_request_total[1h]), 0)[24h:1h]) - 24 * min_over_time(clamp_min(increase(grafana_datasource_request_total[1h]), 0)[24h:1h]), 0))"

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

    # Collect helm chart versions to inject k8s-monitoring version into monitoring spec
    namespace = os.getenv('NAMESPACE', '')
    helm_records = collect_helm_chart_versions(namespace)
    helm_versions = {r['spec']['chart']: r['spec']['chart_version'] for r in helm_records}

    # Format and send image data
    format_and_send_image_data(images, helm_versions)
    logger.info("Completed image data collection and sending")


def collect_and_send_grafana_usage(prometheus_url: str) -> None:
    """
    Collect Grafana usage data from Prometheus and send it to Loki.

    Args:
        prometheus_url: URL of the Prometheus instance
    """
    logger.info("Collecting and sending Grafana usage data")

    # Collect Grafana usage
    grafana_usage = collect_grafana_usage(prometheus_url)

    # Format and send Grafana usage data
    format_and_send_grafana_usage_data(grafana_usage)
    logger.info("Completed Grafana usage data collection and sending")


def _build_stat_lookup(pod_usage_stats: Dict[str, Any]) -> Dict[str, Dict[Tuple[str, str, str], float]]:
    """Build O(1) lookup dicts for each pod usage stat."""
    stat_lookup: Dict[str, Dict[Tuple[str, str, str], float]] = {}
    for stat_name, stat_data in pod_usage_stats.items():
        lookup: Dict[Tuple[str, str, str], float] = {}
        for record in (stat_data or {}).get('data', {}).get('result', []):
            m = record.get('metric', {})
            cluster = m.get('cluster')
            namespace = m.get('namespace')
            pod = m.get('pod')
            if cluster and namespace and pod:
                try:
                    lookup[(cluster, namespace, pod)] = float(record['value'][1])
                except (KeyError, IndexError, ValueError):
                    pass
        stat_lookup[stat_name] = lookup
    return stat_lookup


def _fetch_all_node_stats(prometheus_url: str) -> Dict[Tuple[str, str], Dict[str, Optional[float]]]:
    """
    Fetch CPU and memory utilisation stats for all nodes in 6 batch queries
    (instead of 6 queries per individual node).

    Returns:
        Dict keyed by (cluster, instance) -> {stat_name: value}
    """
    interval = "[24h:5m]"
    cpu_base = (
        "sum without (mode) ("
        "  avg without (cpu) ("
        '    rate(node_cpu_seconds_total{job=~"integrations/(node_exporter|unix)", mode!="idle"}[2m])'
        "  )"
        ") * 100"
    )
    mem_base = (
        "100 - ("
        '  avg by (instance, cluster) (node_memory_MemAvailable_bytes{job=~"integrations/(node_exporter|unix)"})'
        "  /"
        '  avg by (instance, cluster) (node_memory_MemTotal_bytes{job=~"integrations/(node_exporter|unix)"})'
        "  * 100"
        ")"
    )

    queries = {
        "cpu_percent_avg": f"avg by (instance, cluster) (avg_over_time(({cpu_base}){interval}))",
        "cpu_percent_min": f"min by (instance, cluster) (min_over_time(({cpu_base}){interval}))",
        "cpu_percent_max": f"max by (instance, cluster) (max_over_time(({cpu_base}){interval}))",
        "memory_percent_avg": f"avg by (instance, cluster) (avg_over_time(({mem_base}){interval}))",
        "memory_percent_min": f"min by (instance, cluster) (min_over_time(({mem_base}){interval}))",
        "memory_percent_max": f"max by (instance, cluster) (max_over_time(({mem_base}){interval}))",
    }

    node_stats: Dict[Tuple[str, str], Dict[str, Optional[float]]] = {}
    for stat_name, query in queries.items():
        data = query_prometheus(prometheus_url, query)
        if not data:
            continue
        for record in data.get('data', {}).get('result', []):
            instance = record['metric'].get('instance')
            cluster = record['metric'].get('cluster')
            if not instance or not cluster:
                continue
            key = (cluster, instance)
            if key not in node_stats:
                node_stats[key] = {}
            try:
                node_stats[key][stat_name] = float(record['value'][1])
            except (KeyError, IndexError, ValueError):
                node_stats[key][stat_name] = None

    return node_stats


def collect_and_send_otel_pod_node_usage(prometheus_url: str, labels: dict) -> None:
    """
    Collects 24h pod/node resource stats and otel_node_count for all clusters/namespaces;
    sends them in a *single* Loki push (chunked), one log-line per resource, in your requested JSON format.
    """
    logger.info("Collecting 24h OTEL pod/node usage statistics")
    namespace_regex = labels.get('namespace_filter', '.*otel.*')

    # 1. Excluded pods (DaemonSet/Job)
    def excluded_pods_by_owner_kind(kind):
        query = f'kube_pod_owner{{namespace=~"{namespace_regex}",owner_kind="{kind}"}}'
        response = query_prometheus(prometheus_url, query) or {}
        return {(m['metric'].get('cluster'), m['metric'].get('namespace'), m['metric'].get('pod'))
                for m in response.get("data", {}).get("result", [])}

    daemonset_pods = excluded_pods_by_owner_kind("DaemonSet")
    job_pods = excluded_pods_by_owner_kind("Job")
    excluded_pods = daemonset_pods | job_pods

    # 2. Pod-to-node mapping
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

    # 3. Node -> instance_type mapping
    instance_type_query = 'kube_node_labels{job="integrations/kubernetes/kube-state-metrics"}'
    instance_type_data = query_prometheus(prometheus_url, instance_type_query) or {}
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
    pod_usage_stats = {k: query_prometheus(prometheus_url, query) for k, query in promql_templates.items()}

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

    # Fetch all node stats in 6 batch queries instead of 6 queries × N nodes
    all_node_stats = _fetch_all_node_stats(prometheus_url)

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
    Collect Helm chart versions from HelmRelease resources via the Kubernetes API.

    Uses the in-cluster service account token to query the K8s API for
    HelmRelease resources and extract chart name, version, and ready status.

    Returns:
        List of dicts with chart version info per HelmRelease.
    """
    logger.info("Collecting Helm chart versions from Kubernetes API")
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

    url = f"https://{k8s_host}:{k8s_port}/apis/helm.toolkit.fluxcd.io/v2/namespaces/{namespace}/helmreleases"
    headers = {'Authorization': f'Bearer {token}'}

    try:
        response = requests.get(url, headers=headers, verify=ca_path, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        items = response.json().get('items', [])
        for hr in items:
            name = hr['metadata']['name']
            chart_spec = hr.get('spec', {}).get('chart', {}).get('spec', {})
            chart = chart_spec.get('chart')
            version = chart_spec.get('version')
            conditions = hr.get('status', {}).get('conditions', [])
            ready = next((c['status'] for c in conditions if c['type'] == 'Ready'), 'Unknown')
            records.append({
                "metadata": {"cluster": os.getenv('CLUSTER', ''), "namespace": namespace},
                "spec": {
                    "release": name,
                    "chart": chart,
                    "chart_version": version,
                    "ready": ready
                }
            })
        logger.info(f"Collected {len(records)} HelmRelease chart version records")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying Kubernetes API for HelmReleases: {e}")

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
    namespace = os.getenv('NAMESPACE', '')

    # Collect and send data to Loki
    collect_and_send_version_data(prometheus_url, labels)
    collect_and_send_grafana_usage(prometheus_url)
    collect_and_send_otel_pod_node_usage(prometheus_url, labels)
    collect_and_send_helm_chart_versions(namespace)

    logger.info("Completed monitoring data collection")


if __name__ == "__main__":
    main()
