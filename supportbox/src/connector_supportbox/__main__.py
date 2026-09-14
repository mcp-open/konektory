from openmcp_connector_runtime import run_connector_main

from .app import build_definition, create_runtime_app

if __name__ == "__main__":
    run_connector_main(build_definition, create_runtime_app, default_port=8116)
