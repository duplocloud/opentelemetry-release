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
    query = (
    "sum by (datasource) (clamp_min(increase(grafana_datasource_request_total[24h]) - "
    "24 * min_over_time(increase(grafana_datasource_request_total[1h])[24h:1h]), 0))"
    )

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
    
    logger.info("Completed monitoring data collection")


if __name__ == "__main__":
    main() 
