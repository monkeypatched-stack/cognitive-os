"""
Setup script for the Monkeypatched Adapter SDK.

Install in development mode::

    pip install -e sdk/python/

Install from PyPI (future)::

    pip install monkeypatched-sdk
"""

from setuptools import find_packages, setup

setup(
    name="monkeypatched-sdk",
    version="1.0.0",
    description="Adapter SDK for connecting external systems to the Monkeypatched platform",
    long_description=(open("../../README.md").read() if __import__("pathlib").Path("../../README.md").exists() else ""),
    author="Monkeypatched",
    python_requires=">=3.10",
    packages=find_packages(exclude=["examples*", "tests*"]),
    install_requires=[
        "PyYAML>=6.0",
        "httpx>=0.24",
    ],
    extras_require={
        "prometheus": ["prometheus_client>=0.16"],
        "influx": ["influxdb-client>=1.36"],
        "jaeger": [
            "opentelemetry-sdk>=1.20",
            "opentelemetry-exporter-otlp-proto-grpc>=1.20",
        ],
        "mqtt": ["paho-mqtt>=1.6"],
        "opcua": ["asyncua>=0.9"],
        "postgres": ["asyncpg>=0.27"],
        "mongo": ["motor>=3.1"],
        "neo4j": ["neo4j>=5.0"],
        "jwt": ["python-jose[cryptography]>=3.3"],
        "all": [
            "prometheus_client>=0.16",
            "influxdb-client>=1.36",
            "paho-mqtt>=1.6",
            "asyncpg>=0.27",
            "motor>=3.1",
            "python-jose[cryptography]>=3.3",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries",
    ],
)
