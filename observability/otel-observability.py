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


def extract_monitoring_images(prometheus_response: Dict[str, Any]) -> Optional[Dict[str, Dict[str, str]]]:
    """
    Extract image information for monitoring components and daemonsets.
    
    Args:
        prometheus_response: Response from Prometheus containing container information
        
    Returns:
        Dictionary with image information categorized by 'main' and 'monitoring' or None if extraction fails
    """
    try:
        logger.info("Extracting monitoring images from Prometheus response")
        images = {
            'main': {},       # For non-monitoring components
            'monitoring': {}  # For all duplo-monitoring components
        }
        
        for result in prometheus_response['data']['result']:
            container = result['metric']['container']
            image = result['metric']['image']
            pod = result['metric']['pod']
            
            # Determine category based on pod name
            category = 'monitoring' if pod.startswith('duplo-monitoring-') else 'main'
            
            # Map container names to their service names
            service_name = None
            
            # Check for monitoring components
            if container == 'loki':
                service_name = 'loki'
            elif container in ['ingester', 'distributor', 'compactor', 'querier', 'query-frontend', 'ruler', 'store-gateway']:
                if 'tempo' in image:
                    service_name = 'tempo'
                else:
                    service_name = 'mimir'
            elif container == 'pyroscope':
                service_name = 'pyroscope'
            elif container == 'beyla':
                service_name = 'beyla'
            elif container == 'node-exporter':
                service_name = 'node-exporter'
            elif container == 'alloy':
                if 'profiles' in pod:
                    service_name = 'alloy-profiles'
                elif 'logs' in pod:
                    service_name = 'alloy-logs'
                elif 'events' in pod:
                    service_name = 'alloy-events'
                else:
                    service_name = 'alloy-core'
            elif container == 'kube-state-metrics':
                service_name = 'kube-state-metrics'
            
            if service_name:
                images[category][service_name] = image
                logger.debug(f"Added image for service {service_name} in category {category}")
                
        logger.info(f"Successfully extracted images for {len(images['main']) + len(images['monitoring'])} services")
        return images
    except (KeyError, IndexError) as e:
        logger.error(f"Error extracting monitoring images: {e}")
        return None


def query_grafana_usage(prometheus_url: str) -> Optional[Dict[str, int]]:
    """
    Query Grafana datasource usage from Prometheus.
    
    Args:
        prometheus_url: URL of the Prometheus instance
        
    Returns:
        Dictionary with datasource names as keys and usage counts as values, or None if query fails
    """
    query = 'sum by (datasource) (increase(grafana_datasource_request_duration_seconds_count[24h]))'
    logger.info("Querying Grafana datasource usage")
    
    # Use the query_prometheus function instead of duplicating the logic
    data = query_prometheus(prometheus_url, query)
    if not data:
        return None
    
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
        return None


def send_to_loki(
    loki_url: str, 
    images: Dict[str, Dict[str, str]], 
    grafana_usage: Dict[str, int], 
    labels: Dict[str, str]
) -> None:
    """
    Send data to Loki in JSON format with additional labels.
    
    Args:
        loki_url: URL of the Loki instance
        images: Dictionary containing image information for different services
        grafana_usage: Dictionary containing Grafana datasource usage information
        labels: Dictionary containing additional labels to add to the Loki streams
    """
    logger.info("Preparing data to send to Loki")
    current_time = int(time.time() * 1000000000)  # Current time in nanoseconds
    
    # Get Loki credentials from environment
    loki_username = os.getenv('LOKI_USERNAME')
    loki_password = os.getenv('LOKI_PASSWORD')
    
    # Prepare the payload with additional labels
    payload = {
        "streams": [
            {
                "stream": {
                    "job": "monitoring_images",
                    "source": "prometheus",
                    "type": "main",
                    "cluster": labels['cluster'],
                    "namespace": labels['namespace'],
                    "customer": labels['customer'],
                    "environment": labels['environment'],
                    "duplo_url": labels['duplo_url']
                },
                "values": [
                    [str(current_time), json.dumps(images['main'])]
                ]
            },
            {
                "stream": {
                    "job": "monitoring_images",
                    "source": "prometheus",
                    "type": "monitoring",
                    "cluster": labels['cluster'],
                    "namespace": labels['namespace'],
                    "customer": labels['customer'],
                    "environment": labels['environment'],
                    "duplo_url": labels['duplo_url']
                },
                "values": [
                    [str(current_time), json.dumps(images['monitoring'])]
                ]
            },
            {
                "stream": {
                    "job": "grafana_usage",
                    "source": "prometheus",
                    "type": "datasource_usage",
                    "cluster": labels['cluster'],
                    "namespace": labels['namespace'],
                    "customer": labels['customer'],
                    "environment": labels['environment'],
                    "duplo_url": labels['duplo_url']
                },
                "values": [
                    [str(current_time), json.dumps(grafana_usage)]
                ]
            }
        ]
    }

    # Log the payload for debugging
    logger.debug(f"Loki payload: {json.dumps(payload, indent=2)}")

    try:
        logger.info("Sending data to Loki")
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
        logger.info("Successfully sent monitoring data to Loki")
        logger.debug(f"Loki response status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending data to Loki: {e}")


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
        'duplo_url': os.getenv('DUPLO_URL', '')
    }
    
    # Validate required environment variables
    required_vars = [
        'PROMETHEUS_URL', 'LOKI_URL', 'CLUSTER', 'NAMESPACE', 
        'CUSTOMER', 'ENVIRONMENT', 'DUPLO_URL', 
        'LOKI_USERNAME', 'LOKI_PASSWORD'
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return False, labels, missing_vars
    
    return True, labels, []


def main() -> None:
    """
    Main function that orchestrates the monitoring data collection process.
    
    This function:
    1. Retrieves configuration from environment variables
    2. Validates required environment variables
    3. Queries Prometheus for container images
    4. Extracts monitoring images
    5. Queries Grafana usage
    6. Sends all data to Loki
    """
    logger.info("Starting monitoring data collection")
    
    # Validate environment variables
    is_valid, labels, missing_vars = validate_environment_variables()
    if not is_valid:
        return
    
    # Get configuration from environment
    prometheus_url = os.getenv('PROMETHEUS_URL')
    loki_url = os.getenv('LOKI_URL')
    
    # PromQL query for all components
    query = f'''
    count by(container, image, pod) (
      kube_pod_container_info{{
        namespace="{labels['namespace']}",
        container=~"loki|ingester|distributor|compactor|querier|query-frontend|ruler|store-gateway|pyroscope|beyla|node-exporter|alloy|kube-state-metrics",
        pod=~"duplo-(logging|metrics|tracing|profiling)-.+|duplo-monitoring-.+"
      }}
    )
    '''
    
    # Query Prometheus for container images
    prometheus_response = query_prometheus(prometheus_url, query)
    if not prometheus_response:
        return
    
    # Extract monitoring images
    images = extract_monitoring_images(prometheus_response)
    if not images:
        return
    
    # Query Grafana usage
    grafana_usage = query_grafana_usage(prometheus_url)
    if not grafana_usage:
        logger.warning("Could not fetch Grafana usage data")
        grafana_usage = {}
    
    # Send to Loki with additional labels
    send_to_loki(loki_url, images, grafana_usage, labels)
    logger.info("Completed monitoring data collection")


if __name__ == "__main__":
    main() 