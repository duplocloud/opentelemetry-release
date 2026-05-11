#!/usr/bin/env python3
"""
Collector Stack Identity Ping

Pushes a single identity log line to Loki for collector-only AOS deployments.
The main stack's otel-observability.py already collects all real metrics data
via central Mimir. This script exists solely to register the collector deployment's
identity labels (grafana_url, duplo_url, central_duplo_url, stack_type=collector)
so it appears correctly in the Customer Overview dashboard.
"""

import json
import logging
import os
import time

import requests

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS = [
    'LOKI_URL',
    'CLUSTER',
    'NAMESPACE',
    'CUSTOMER',
    'ENVIRONMENT',
    'DUPLO_URL',
]


def validate_environment_variables() -> dict:
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

    return {
        'loki_url': os.getenv('LOKI_URL'),
        'cluster': os.getenv('CLUSTER'),
        'namespace': os.getenv('NAMESPACE'),
        'customer': os.getenv('CUSTOMER'),
        'environment': os.getenv('ENVIRONMENT'),
        'duplo_url': os.getenv('DUPLO_URL'),
        'grafana_url': os.getenv('GRAFANA_URL', ''),
        'central_duplo_url': os.getenv('CENTRAL_DUPLO_URL', ''),
        'job_version': os.getenv('JOB_VERSION', ''),
        'stack_type': os.getenv('STACK_TYPE', 'collector'),
    }


def send_to_loki(loki_url: str, stream_labels: dict, log_line: str) -> bool:
    timestamp_ns = str(int(time.time() * 1e9))
    payload = {
        'streams': [
            {
                'stream': stream_labels,
                'values': [[timestamp_ns, log_line]],
            }
        ]
    }
    try:
        response = requests.post(
            f"{loki_url}/loki/api/v1/push",
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload),
            timeout=30,
        )
        response.raise_for_status()
        logger.info("Successfully pushed identity log to Loki")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Error pushing to Loki: {e}")
        return False


def main():
    env = validate_environment_variables()

    stream_labels = {
        'job': 'collector_identity',
        'cluster': env['cluster'],
        'namespace': env['namespace'],
        'customer': env['customer'],
        'environment': env['environment'],
        'duplo_url': env['duplo_url'],
        'grafana_url': env['grafana_url'],
        'central_duplo_url': env['central_duplo_url'],
        'job_version': env['job_version'],
        'stack_type': env['stack_type'],
    }

    log_line = json.dumps({
        'message': 'collector identity ping',
        'cluster': env['cluster'],
        'namespace': env['namespace'],
        'customer': env['customer'],
        'environment': env['environment'],
        'duplo_url': env['duplo_url'],
        'grafana_url': env['grafana_url'],
        'central_duplo_url': env['central_duplo_url'],
        'job_version': env['job_version'],
        'stack_type': env['stack_type'],
    })

    success = send_to_loki(env['loki_url'], stream_labels, log_line)
    if not success:
        raise RuntimeError("Failed to push identity log to Loki")


if __name__ == '__main__':
    main()
