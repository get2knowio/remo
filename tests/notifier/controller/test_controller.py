"""Controller core tests (Approach B — issue #46), driven by fakes (no daemon)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from remo_cli.notifier.controller import Controller, ContainerInfo, ControllerConfig, DockerEvent
from remo_cli.notifier.models import SourceRegistration


class FakeDocker:
    def __init__(self) -> None:
        self.containers: dict[str, ContainerInfo] = {}
        self.networks: dict[str, list[str]] = {}  # net → member names
        self.event_log: list[DockerEvent] = []
        self.actions: list[str] = []  # connect/disconnect record

    # -- test scaffolding --
    def add_container(
        self, cid: str, *, name: str, labels: dict[str, str], networks: list[str]
    ) -> None:
        self.containers[cid] = ContainerInfo(id=cid, name=name, labels=labels, networks=networks)
        for net in networks:
            self.networks.setdefault(net, [])
            if name not in self.networks[net]:
                self.networks[net].append(name)

    # -- DockerClient protocol --
    async def events(self) -> AsyncIterator[DockerEvent]:
        for ev in self.event_log:
            yield ev

    async def list_containers(self) -> list[str]:
        return list(self.containers)

    async def inspect_container(self, cid: str) -> ContainerInfo | None:
        return self.containers.get(cid)

    async def network_members(self, net: str) -> list[str]:
        return list(self.networks.get(net, []))

    async def network_connect(self, net: str, container: str) -> None:
        self.actions.append(f"connect {net} {container}")
        self.networks.setdefault(net, []).append(container)

    async def network_disconnect(self, net: str, container: str) -> None:
        self.actions.append(f"disconnect {net} {container}")
        self.networks.get(net, []).remove(container)


class FakeRegistrar:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.registered: list[SourceRegistration] = []
        self.deregistered: list[str] = []

    async def register(self, reg: SourceRegistration) -> bool:
        if self.ok:
            self.registered.append(reg)
        return self.ok

    async def deregister(self, source_id: str) -> None:
        self.deregistered.append(source_id)


def _cfg(tmp_path: Path, **kw) -> ControllerConfig:
    return ControllerConfig(key_dir=tmp_path, **kw)


def _key(tmp_path: Path, source_id: str, value: str = "k") -> None:
    (tmp_path / source_id).write_text(value + "\n")


def _start(cid: str) -> DockerEvent:
    return DockerEvent(type="container", action="start", actor_id=cid)


def _stop(cid: str) -> DockerEvent:
    return DockerEvent(type="container", action="die", actor_id=cid)


async def test_labeled_start_registers_and_attaches(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-a"])
    _key(tmp_path, "proj-a", "secret")
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).handle(_start("c1"))

    assert len(r.registered) == 1
    reg = r.registered[0]
    assert reg.source_id == "proj-a"
    assert reg.api_url == "http://proj-a:8080"  # derived: scheme://name:default_port
    assert reg.api_key == "secret"
    assert d.actions == ["connect net-a remo-notifier"]


async def test_unlabeled_container_ignored(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={}, networks=["net-a"])
    _key(tmp_path, "proj-a")
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).handle(_start("c1"))
    assert r.registered == []
    assert d.actions == []


async def test_missing_key_fails_closed(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "1"}, networks=["net-a"])
    r = FakeRegistrar()  # no key file written
    await Controller(d, r, _cfg(tmp_path)).handle(_start("c1"))
    assert r.registered == []
    assert d.actions == []  # not attached either — nothing registered


async def test_port_and_source_id_overrides(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container(
        "c1",
        name="container-xyz",
        labels={
            "remo.agentsh.enabled": "true",
            "remo.agentsh.port": "9000",
            "remo.agentsh.source-id": "proj-a",
            "remo.label.project": "proj-a",
            "remo.label.owner": "paul",
        },
        networks=["net-a"],
    )
    _key(tmp_path, "proj-a")
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).handle(_start("c1"))
    reg = r.registered[0]
    assert reg.source_id == "proj-a"  # label wins over container name
    assert reg.api_url == "http://proj-a:9000"
    assert reg.labels == {"project": "proj-a", "owner": "paul"}


async def test_bridge_is_not_attached(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container(
        "c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["bridge", "net-a"]
    )
    _key(tmp_path, "proj-a")
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).handle(_start("c1"))
    assert d.actions == ["connect net-a remo-notifier"]  # bridge skipped


async def test_start_is_idempotent(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-a"])
    _key(tmp_path, "proj-a")
    r = FakeRegistrar()
    ctrl = Controller(d, r, _cfg(tmp_path))
    await ctrl.handle(_start("c1"))
    await ctrl.handle(_start("c1"))  # duplicate
    assert len(r.registered) == 1
    assert d.actions == ["connect net-a remo-notifier"]


async def test_stop_deregisters_and_detaches_when_last(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-a"])
    _key(tmp_path, "proj-a")
    r = FakeRegistrar()
    ctrl = Controller(d, r, _cfg(tmp_path))
    await ctrl.handle(_start("c1"))
    # proj-a gone; only the notifier remains on net-a
    d.networks["net-a"] = ["remo-notifier"]
    await ctrl.handle(_stop("c1"))
    assert r.deregistered == ["proj-a"]
    assert d.actions[-1] == "disconnect net-a remo-notifier"


async def test_stop_keeps_notifier_when_others_remain(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-x"])
    _key(tmp_path, "proj-a")
    r = FakeRegistrar()
    ctrl = Controller(d, r, _cfg(tmp_path))
    await ctrl.handle(_start("c1"))
    # a sibling service (e.g. Compose db) is still on the shared project net
    d.networks["net-x"] = ["remo-notifier", "proj-a-db"]
    await ctrl.handle(_stop("c1"))
    assert r.deregistered == ["proj-a"]
    assert "disconnect net-x remo-notifier" not in d.actions


async def test_stop_for_untracked_is_noop(tmp_path: Path) -> None:
    d = FakeDocker()
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).handle(_stop("ghost"))
    assert r.deregistered == []


async def test_reconcile_adopts_existing_then_run_streams(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-a"])
    d.add_container("c2", name="proj-b", labels={"remo.agentsh.enabled": "true"}, networks=["net-b"])
    _key(tmp_path, "proj-a")
    _key(tmp_path, "proj-b")
    # c2 already up (reconcile), c1 arrives as a live event
    d.containers.pop("c1")
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-a"])
    d.event_log = [_start("c1")]
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).run()
    assert {reg.source_id for reg in r.registered} == {"proj-a", "proj-b"}


async def test_failed_registration_is_not_tracked(tmp_path: Path) -> None:
    d = FakeDocker()
    d.add_container("c1", name="proj-a", labels={"remo.agentsh.enabled": "true"}, networks=["net-a"])
    _key(tmp_path, "proj-a")
    r = FakeRegistrar(ok=False)
    ctrl = Controller(d, r, _cfg(tmp_path))
    await ctrl.handle(_start("c1"))
    # registration failed → not tracked → a later stop is a no-op
    await ctrl.handle(_stop("c1"))
    assert r.deregistered == []


async def test_non_container_events_ignored(tmp_path: Path) -> None:
    d = FakeDocker()
    r = FakeRegistrar()
    await Controller(d, r, _cfg(tmp_path)).handle(
        DockerEvent(type="network", action="connect", actor_id="net-a")
    )
    assert r.registered == []
