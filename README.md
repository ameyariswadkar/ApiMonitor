### API Monitor
## A Distributed AST-Driven Framework for Tracking API Usage Across Codebases

## Overview
This project implements a distributed pipeline that analyzes large codebases to detect:

- Which APIs from libraries like pandas, numpy, requests, etc. are being used
- Whether those APIs are deprecated or will be deprecated in upcoming versions
- How API usage trends evolve over time
- Which repositories are most exposed to breaking changes

It works by scanning source code to extract Abstract Syntax Tree and API call informations, pushing events to Kafka, computing usage statistics with PySpark, and orchestrating the full workflow using Apache Airflow.

This system functions like a lightweight, open-source version of tools such as Sourcegraph Code Insights, Snyk Code, or Semgrep + Looker dashboards, but fully customizable and scalable by design.

## Plan (v0.1)
- [ ] Set up basic project structure
- [ ] Implement simple AST walker for a small repo
- [ ] Wire into Kafka producer/consumer
- [ ] Add Airflow DAG for scheduled runs