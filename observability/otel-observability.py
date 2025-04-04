import requests
import json
from datetime import datetime
import time
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def query_prometheus(prometheus_url, query):
    """Query Prometheus and return the response"""
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

def extract_monitoring_images(prometheus_response):
    """Extract image information for monitoring components and daemonsets"""
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

def query_grafana_usage(prometheus_url):
    """Query Grafana datasource usage from Prometheus"""
    query = 'sum by (datasource) (increase(grafana_datasource_request_duration_seconds_count[24h]))'
    try:
        logger.info("Querying Grafana datasource usage")
        response = requests.get(
            f"{prometheus_url}/api/v1/query",
            params={'query': query}
        )
        response.raise_for_status()
        data = response.json()
        
        # Extract datasource usage data
        usage_data = {}
        if 'data' in data and 'result' in data['data']:
            for result in data['data']['result']:
                datasource = result['metric']['datasource']
                value = float(result['value'][1])  # Get the instant value
                usage_data[datasource] = round(value)  # Round to nearest integer
                logger.debug(f"Datasource {datasource} usage: {usage_data[datasource]}")
        
        logger.info(f"Successfully collected usage data for {len(usage_data)} datasources")
        return usage_data
    except (requests.exceptions.RequestException, KeyError, IndexError, ValueError) as e:
        logger.error(f"Error querying Grafana usage: {e}")
        return None

def send_to_loki(loki_url, images, grafana_usage, labels):
    """Send data to Loki in JSON format with additional labels"""
    logger.info("Preparing data to send to Loki")
    current_time = int(time.time() * 1000000000)  # Current time in nanoseconds
    
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
        response = requests.post(
            f"{loki_url}/loki/api/v1/push",
            json=payload,
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status()
        logger.info("Successfully sent monitoring data to Loki")
        logger.debug(f"Loki response status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending data to Loki: {e}")

def main():
    logger.info("Starting monitoring data collection")
    
    # Configuration from environment variables
    PROMETHEUS_URL = os.getenv('PROMETHEUS_URL')
    LOKI_URL = os.getenv('LOKI_URL')
    
    # Additional labels from environment variables
    labels = {
        'cluster': os.getenv('CLUSTER', ''),
        'namespace': os.getenv('NAMESPACE', ''),
        'customer': os.getenv('CUSTOMER', ''),
        'environment': os.getenv('ENVIRONMENT', ''),
        'duplo_url': os.getenv('DUPLO_URL', '')
    }
    
    # Validate required environment variables
    required_vars = ['PROMETHEUS_URL', 'LOKI_URL', 'CLUSTER', 'NAMESPACE', 'CUSTOMER', 'ENVIRONMENT', 'DUPLO_URL']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return
    
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
    prometheus_response = query_prometheus(PROMETHEUS_URL, query)
    if not prometheus_response:
        return
    
    # Extract monitoring images
    images = extract_monitoring_images(prometheus_response)
    if not images:
        return
    
    # Query Grafana usage
    grafana_usage = query_grafana_usage(PROMETHEUS_URL)
    if not grafana_usage:
        logger.warning("Could not fetch Grafana usage data")
        grafana_usage = {}
    
    # Send to Loki with additional labels
    send_to_loki(LOKI_URL, images, grafana_usage, labels)
    logger.info("Completed monitoring data collection")

if __name__ == "__main__":
    main() 