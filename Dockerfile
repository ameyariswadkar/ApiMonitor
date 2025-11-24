# Dockerfile
# Base on official Apache Airflow image
FROM apache/airflow:2.9.0-python3.11



# ---------------------------------------------------------
# System setup (Java for Spark, curl for uv)
# ---------------------------------------------------------
USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        curl \
        less \
    && rm -rf /var/lib/apt/lists/*

# Set JAVA_HOME for Spark
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# ---------------------------------------------------------
# Install uv (fast Python package manager)
# ---------------------------------------------------------
# This follows the official install snippet from astral.sh.
# It installs uv under /root/.cargo/bin, so we symlink it into /usr/local/bin.

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Switch back to airflow user (Airflow image default)
USER airflow

# ---------------------------------------------------------
# Project deps via uv + lockfile
# ---------------------------------------------------------
# We'll install dependencies *before* copying the whole source tree
# so Docker can cache this layer unless pyproject/lock changes.

# All project code will live here in the container
WORKDIR /opt/api-usage-monitor

# Copy just dependency metadata first
COPY pyproject.toml uv.lock ./

# Copy requirements.txt into the image root
COPY requirements.txt /requirements.txt

# This is the bit you asked about:
#   COPY pyproject.toml uv.lock .
#   RUN uv sync --frozen --system
#
# It goes here, right after we copy the files that define deps.
RUN uv pip install --system --no-cache -r /requirements.txt

# ---------------------------------------------------------
# Copy the rest of your project
# ---------------------------------------------------------
# Assuming your repo root contains:
#   parser/, analyzer/, spark_jobs/, db_loader/, airflow/, configs/, etc.
COPY . /opt/api-usage-monitor

# Make project importable
ENV PYTHONPATH="/opt/api-usage-monitor:${PYTHONPATH}"

# ---------------------------------------------------------
# Expose your Airflow DAG
# ---------------------------------------------------------
# Your DAG file lives at:
#   /opt/api-usage-monitor/airflow/dags/api_usage_monitor_dag.py
# Create airflow user & group *before using chown*

USER root
RUN mkdir -p /opt/api-usage-monitor/results \
    && chmod -R 777 /opt/api-usage-monitor/results

RUN mkdir -p /opt/airflow/dags && \
    ln -s /opt/api-usage-monitor/airflow/dags/api_usage_monitor_dag.py \
          /opt/airflow/dags/api_usage_monitor_dag.py

# Go back to airflow user for runtime
USER airflow

RUN mkdir -p /opt/airflow/dags && \
    ln -s /opt/api-usage-monitor/airflow/dags/api_usage_monitor_dag.py \
          /opt/airflow/dags/api_usage_monitor_dag.py
