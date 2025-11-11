#!/usr/bin/env python3
"""Simple console-based ping monitor.

This script prompts the user for a list of IPv4 addresses or hostnames and then
continuously pings them in parallel, updating a color-coded table showing basic
statistics for each host.
"""
from __future__ import annotations

import ipaddress
import platform
import re
import subprocess
import threading
import time
from typing import Dict, List, Optional


def is_possible_ipv4(value: str) -> bool:
    """Return True if *value* looks like an IPv4 address string."""
    return all(ch.isdigit() or ch == "." for ch in value)


def is_valid_ipv4(address: str) -> bool:
    """Validate an IPv4 address, ensuring each octet is in range."""
    try:
        ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return True


def prompt_targets() -> List[str]:
    """Prompt the user for a list of target hosts."""
    while True:
        raw = input(
            "Enter hostnames or IPv4 addresses (separate with spaces or commas): "
        )
        parts = [part.strip() for part in re.split(r"[\s,]+", raw) if part.strip()]
        if not parts:
            print("No hosts entered. Please try again.\n")
            continue

        invalid_ips: List[str] = []
        valid_hosts: List[str] = []

        for part in parts:
            if is_possible_ipv4(part):
                if is_valid_ipv4(part):
                    valid_hosts.append(part)
                else:
                    invalid_ips.append(part)
            else:
                valid_hosts.append(part)

        if invalid_ips:
            print("The following IPv4 addresses are invalid:")
            for addr in invalid_ips:
                print(f"  - {addr}")
            print("Please re-enter the list of hosts.\n")
            continue

        return valid_hosts


def ping_command(host: str, timeout: int) -> List[str]:
    """Return the ping command appropriate for the current platform."""
    system = platform.system().lower()
    if system == "windows":
        # -n: number of echo requests, -w: timeout in milliseconds
        return ["ping", "-n", "1", "-w", str(timeout * 1000), host]
    # Assume POSIX ping
    # -c: number of packets, -W: timeout in seconds
    return ["ping", "-c", "1", "-W", str(timeout), host]


def parse_latency(output: str) -> Optional[float]:
    """Extract latency in milliseconds from ping output."""
    match = re.search(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", output)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def ping_once(host: str, timeout: int = 2) -> Optional[float]:
    """Ping *host* a single time and return latency in milliseconds if successful."""
    cmd = ping_command(host, timeout)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("The system 'ping' command is not available.")

    if completed.returncode != 0:
        return None

    latency = parse_latency(completed.stdout) or parse_latency(completed.stderr)
    return latency


class HostStats:
    """Maintain ping statistics for a host."""

    def __init__(self) -> None:
        self.sent = 0
        self.received = 0
        self.latency_ms: Optional[float] = None
        self.responding = False

    def record_result(self, success: bool, latency: Optional[float]) -> None:
        self.sent += 1
        if success:
            self.received += 1
            self.latency_ms = latency
            self.responding = True
        else:
            self.latency_ms = None
            self.responding = False

    def success_rate(self) -> float:
        if self.sent == 0:
            return 0.0
        return (self.received / self.sent) * 100

    def clone(self) -> "HostStats":
        duplicate = HostStats()
        duplicate.sent = self.sent
        duplicate.received = self.received
        duplicate.latency_ms = self.latency_ms
        duplicate.responding = self.responding
        return duplicate


def ping_worker(
    host: str,
    stats: Dict[str, HostStats],
    lock: threading.Lock,
    stop_event: threading.Event,
    interval: int,
    timeout: int,
) -> None:
    while not stop_event.is_set():
        latency = ping_once(host, timeout=timeout)
        success = latency is not None
        with lock:
            stats[host].record_result(success, latency)
        if stop_event.wait(interval):
            break


def clear_screen() -> None:
    # Use ANSI escape codes to clear the terminal instead of shelling out to
    # external commands, which keeps the monitor portable across systems.
    print("\033[2J\033[H", end="", flush=True)


GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def render_table(snapshot: Dict[str, HostStats]) -> None:
    clear_screen()
    header = f"{'Address':<30} {'Sent':>6} {'Recv':>6} {'Success %':>10} {'Latency':>12}"
    print(header)
    print("-" * len(header))

    for host, stat in snapshot.items():
        latency_display = (
            f"{stat.latency_ms:.2f} ms" if stat.latency_ms is not None else "--"
        )
        row = (
            f"{host:<30} "
            f"{stat.sent:>6} "
            f"{stat.received:>6} "
            f"{stat.success_rate():>9.1f}% "
            f"{latency_display:>12}"
        )
        color = GREEN if stat.responding else RED
        print(f"{color}{row}{RESET}")

    print("\nPress Ctrl+C to stop monitoring.")


def main() -> None:
    hosts = prompt_targets()
    stats: Dict[str, HostStats] = {host: HostStats() for host in hosts}
    lock = threading.Lock()
    stop_event = threading.Event()

    interval = 5  # seconds between pings
    timeout = 2   # seconds to wait for a response

    threads = [
        threading.Thread(
            target=ping_worker,
            args=(host, stats, lock, stop_event, interval, timeout),
            daemon=True,
        )
        for host in hosts
    ]

    for thread in threads:
        thread.start()

    try:
        while True:
            time.sleep(1)
            with lock:
                snapshot = {host: stat.clone() for host, stat in stats.items()}
            render_table(snapshot)
    except KeyboardInterrupt:
        stop_event.set()
        for thread in threads:
            thread.join()
        with lock:
            snapshot = {host: stat.clone() for host, stat in stats.items()}
        clear_screen()
        render_table(snapshot)
        print("\nMonitoring stopped.")


if __name__ == "__main__":
    main()
