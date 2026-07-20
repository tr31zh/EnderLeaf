import subprocess
import sys
import time

NUM_NODES = 6
PORTS = [i + 8760 for i in range(NUM_NODES)]

NODES = [f"ws://localhost:{p}" for p in PORTS]

def start_test_nodes():
    processes = []
    for port in PORTS:
        p = subprocess.Popen([sys.executable, "ws_node.py", str(port)])
        processes.append(p)
    return processes


def main():
    print(f"Setting up {NUM_NODES} local test clients...")
    nodes = start_test_nodes()
    nodes[0].wait()
    print("Ending script")
    time.sleep(2)  # Allow nodes to bind to ports


if __name__ == "__main__":
    main()
