"""
Collection of all simple API testing pipelines for the LANraragi server.

For each testing pipeline, a corresponding LRR environment is set up and torn down.

windows-2025 dev environments on Github are extremely flaky and prone to network problems.
We add a flake tank at the front, and rerun test cases in Windows on flake errors.
"""
