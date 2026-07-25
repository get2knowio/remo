"""Performance regression test for the registry v2 accessor (015-registry-v2, T035).

SC-008 (spec.md, quickstart.md §8): registry read+validate+write overhead
stays under 100ms per command invocation at 200 entries. Catches an
accidentally-quadratic validation or serialization path before it ships.
"""

from __future__ import annotations

import time

from remo_cli.core.registry import mutate_registry, read_registry, validate_hosts
from remo_cli.models.host import KnownHost

_TYPES = ("incus", "proxmox", "aws", "hetzner", "ssh")


def _generate_hosts(count: int) -> list[KnownHost]:
    hosts: list[KnownHost] = []
    for i in range(count):
        type_ = _TYPES[i % len(_TYPES)]
        if type_ == "incus":
            hosts.append(
                KnownHost(
                    type=type_, name=f"host{i}/dev{i}", host=f"dev{i}.incus",
                    user="remo", instance_id="paul", access_mode="direct",
                )
            )
        elif type_ == "proxmox":
            hosts.append(
                KnownHost(
                    type=type_, name=f"pve{i}/dev{i}", host=f"10.0.{i // 256}.{i % 256}",
                    user="remo", instance_id=str(100 + i), access_mode="direct",
                    region="root",
                )
            )
        elif type_ == "aws":
            hosts.append(
                KnownHost(
                    type=type_, name=f"cloud{i}", host=f"203.0.113.{i % 256}",
                    user="remo", instance_id=f"i-{i:017x}", access_mode="ssm",
                    region="us-east-1",
                )
            )
        elif type_ == "hetzner":
            hosts.append(
                KnownHost(
                    type=type_, name=f"web{i}", host=f"198.51.100.{i % 256}",
                    user="remo", access_mode="direct",
                )
            )
        else:  # ssh
            hosts.append(
                KnownHost(
                    type=type_, name=f"box{i}", host=f"box{i}.lan", user="admin",
                    instance_id=str(2200 + (i % 100)), access_mode="direct",
                    region=f"/home/paul/.ssh/id_{i}",
                )
            )
    return hosts


def test_200_entry_round_trip_under_100ms(tmp_config_dir):
    hosts = _generate_hosts(200)

    start = time.perf_counter()
    validate_hosts(hosts)
    mutate_registry(lambda _current: hosts)
    view = read_registry()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(view.hosts) == 200
    assert elapsed_ms < 100, f"200-entry registry round-trip took {elapsed_ms:.1f}ms (budget: 100ms)"


def test_200_entry_read_only_is_well_under_budget(tmp_config_dir):
    hosts = _generate_hosts(200)
    mutate_registry(lambda _current: hosts)

    start = time.perf_counter()
    for _ in range(10):
        view = read_registry()
    elapsed_ms = ((time.perf_counter() - start) / 10) * 1000

    assert len(view.hosts) == 200
    assert elapsed_ms < 100, f"200-entry registry read averaged {elapsed_ms:.1f}ms (budget: 100ms)"
