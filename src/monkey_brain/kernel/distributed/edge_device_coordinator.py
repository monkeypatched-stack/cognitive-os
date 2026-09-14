"""Phase 12: Distributed Execution - Edge Device Support

Enables actors to execute on edge devices, not just central coordination.
Each edge device hosts local actors and syncs with central Context Stream.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from enum import Enum


class DeviceType(str, Enum):
    """Types of execution devices"""

    CENTRAL = "central"  # Central server
    EDGE = "edge"  # Edge device (field, robot, etc)
    MOBILE = "mobile"  # Mobile device


class SyncStrategy(str, Enum):
    """Strategies for syncing state with central"""

    EAGER = "eager"  # Sync immediately
    BATCHED = "batched"  # Batch updates
    PERIODIC = "periodic"  # Sync on interval
    ON_CRITICAL = "on_critical"  # Only sync critical events


@dataclass
class EdgeDevice:
    """Represents an edge device in distributed system"""

    device_id: str
    device_type: DeviceType
    region: str
    location: str
    capacity: int  # Max actors this device can host
    hosted_actors: Set[str] = field(default_factory=set)
    is_connected: bool = True
    last_sync: datetime = field(default_factory=datetime.now)
    sync_strategy: SyncStrategy = SyncStrategy.BATCHED
    pending_events: List[Dict] = field(default_factory=list)
    pending_observations: List[Dict] = field(default_factory=list)

    def can_host(self) -> bool:
        """Check if device can host more actors"""
        return len(self.hosted_actors) < self.capacity

    def register_actor(self, actor_id: str) -> bool:
        """Register actor to run on this device"""
        if not self.can_host():
            return False
        self.hosted_actors.add(actor_id)
        return True

    def unregister_actor(self, actor_id: str) -> bool:
        """Unregister actor from device"""
        if actor_id not in self.hosted_actors:
            return False
        self.hosted_actors.remove(actor_id)
        return True

    def queue_event(self, event: Dict) -> None:
        """Queue event for sync"""
        self.pending_events.append(event)

    def queue_observation(self, observation: Dict) -> None:
        """Queue observation for sync"""
        self.pending_observations.append(observation)

    def should_sync(self) -> bool:
        """Determine if should sync with central"""
        if self.sync_strategy == SyncStrategy.EAGER:
            return len(self.pending_events) > 0

        if self.sync_strategy == SyncStrategy.ON_CRITICAL:
            return any(e.get("critical", False) for e in self.pending_events)

        # BATCHED and PERIODIC return True only when checked
        return True

    def mark_synced(self) -> None:
        """Clear pending queues after sync"""
        self.pending_events.clear()
        self.pending_observations.clear()
        self.last_sync = datetime.now()


@dataclass
class DeviceCluster:
    """Groups devices in geographic region"""

    cluster_id: str
    region: str
    central_device: Optional[str] = None  # Central hub for region
    devices: Dict[str, EdgeDevice] = field(default_factory=dict)
    cluster_coordinator: Optional[str] = None

    def add_device(self, device: EdgeDevice) -> None:
        """Add device to cluster"""
        self.devices[device.device_id] = device

    def get_devices_for_actor_type(self, actor_type: str) -> List[EdgeDevice]:
        """Get devices suitable for hosting actor type"""
        # In real implementation, would consider capabilities
        return [d for d in self.devices.values() if d.can_host()]

    def is_connected(self) -> bool:
        """Check if cluster is connected to central"""
        if not self.central_device:
            return False
        central = self.devices.get(self.central_device)
        return central and central.is_connected

    def get_capacity(self) -> Dict[str, int]:
        """Get available capacity summary"""
        return {device_id: device.capacity - len(device.hosted_actors) for device_id, device in self.devices.items()}


class DistributedExecutionCoordinator:
    """Coordinates execution across edge devices

    Enables:
    - Placement of actors on edge devices
    - Syncing state between edge and central
    - Geographic locality optimization
    - Offline capability (edge continues when disconnected)
    """

    def __init__(self):
        self._devices: Dict[str, EdgeDevice] = {}
        self._clusters: Dict[str, DeviceCluster] = {}
        self._actor_placement: Dict[str, str] = {}  # actor_id -> device_id
        self._pending_syncs: asyncio.Queue = asyncio.Queue()
        self._sync_in_progress: bool = False
        self._central_device_id: Optional[str] = None
        self._event_log: List[Dict] = []
        self._max_log_size = 10000

    def register_device(self, device: EdgeDevice) -> None:
        """Register edge device in network"""
        self._devices[device.device_id] = device

        # Add to cluster
        if device.region not in self._clusters:
            self._clusters[device.region] = DeviceCluster(cluster_id=f"cluster_{device.region}", region=device.region)

        self._clusters[device.region].add_device(device)

        # Mark as central if first device
        if not self._central_device_id:
            self._central_device_id = device.device_id

    def unregister_device(self, device_id: str) -> bool:
        """Remove device from network"""
        device = self._devices.pop(device_id, None)
        if not device:
            return False

        # Remove from cluster
        cluster = self._clusters.get(device.region)
        if cluster:
            cluster.devices.pop(device_id, None)

        # Migrate actors from device
        for actor_id in list(device.hosted_actors):
            self._migrate_actor(actor_id, device_id)

        return True

    def place_actor_on_device(self, actor_id: str, device_id: str) -> bool:
        """Place actor on specific device"""
        device = self._devices.get(device_id)
        if not device or not device.can_host():
            return False

        # Remove from old device if present
        old_device_id = self._actor_placement.get(actor_id)
        if old_device_id:
            old_device = self._devices.get(old_device_id)
            if old_device:
                old_device.unregister_actor(actor_id)

        # Add to new device
        device.register_actor(actor_id)
        self._actor_placement[actor_id] = device_id

        return True

    def place_actor_in_region(self, actor_id: str, region: str) -> bool:
        """Place actor in specific region (choose best device)"""
        cluster = self._clusters.get(region)
        if not cluster:
            return False

        # Find device with most capacity
        available = cluster.get_devices_for_actor_type("*")
        if not available:
            return False

        best_device = max(available, key=lambda d: d.capacity - len(d.hosted_actors))

        return self.place_actor_on_device(actor_id, best_device.device_id)

    def get_actor_location(self, actor_id: str) -> Optional[str]:
        """Get device hosting actor"""
        return self._actor_placement.get(actor_id)

    def get_device_load(self, device_id: str) -> Optional[float]:
        """Get load (0.0-1.0) on device"""
        device = self._devices.get(device_id)
        if not device:
            return None
        return len(device.hosted_actors) / device.capacity

    async def sync_device_to_central(self, device_id: str) -> bool:
        """Sync edge device state to central"""
        device = self._devices.get(device_id)
        if not device or not device.is_connected:
            return False

        # Queue sync
        await self._pending_syncs.put(
            {
                "device_id": device_id,
                "events": device.pending_events.copy(),
                "observations": device.pending_observations.copy(),
                "timestamp": datetime.now(),
            }
        )

        device.mark_synced()
        return True

    async def sync_central_to_device(self, device_id: str, events: List[Dict]) -> bool:
        """Push events from central to edge device"""
        device = self._devices.get(device_id)
        if not device or not device.is_connected:
            return False

        # Queue events for device
        for event in events:
            device.queue_event(event)

        return True

    async def handle_device_disconnection(self, device_id: str) -> None:
        """Handle edge device going offline"""
        device = self._devices.get(device_id)
        if not device:
            return

        device.is_connected = False

        # Actors can continue locally until reconnection
        # Events will be synced when connection restored

    async def handle_device_reconnection(self, device_id: str) -> None:
        """Handle edge device coming back online"""
        device = self._devices.get(device_id)
        if not device:
            return

        device.is_connected = True

        # Sync pending events
        await self.sync_device_to_central(device_id)

    def _migrate_actor(self, actor_id: str, from_device_id: str, to_device_id: Optional[str] = None) -> bool:
        """Migrate actor from one device to another"""
        if to_device_id is None:
            # Find best device
            available = [d for d in self._devices.values() if d.can_host() and d.device_id != from_device_id]
            if not available:
                return False
            to_device_id = max(available, key=lambda d: d.capacity - len(d.hosted_actors)).device_id

        return self.place_actor_on_device(actor_id, to_device_id)

    def get_cluster_stats(self, region: str) -> Optional[Dict[str, Any]]:
        """Get statistics for cluster"""
        cluster = self._clusters.get(region)
        if not cluster:
            return None

        total_capacity = sum(d.capacity for d in cluster.devices.values())
        total_hosted = sum(len(d.hosted_actors) for d in cluster.devices.values())

        return {
            "region": region,
            "device_count": len(cluster.devices),
            "total_capacity": total_capacity,
            "hosted_actors": total_hosted,
            "available_capacity": total_capacity - total_hosted,
            "connected": cluster.is_connected(),
        }

    def get_global_stats(self) -> Dict[str, Any]:
        """Get global distributed system statistics"""
        total_devices = len(self._devices)
        total_actors = len(self._actor_placement)
        total_capacity = sum(d.capacity for d in self._devices.values())
        connected_devices = sum(1 for d in self._devices.values() if d.is_connected)

        return {
            "total_devices": total_devices,
            "connected_devices": connected_devices,
            "total_actors": total_actors,
            "total_capacity": total_capacity,
            "utilization": total_actors / total_capacity if total_capacity > 0 else 0,
            "clusters": len(self._clusters),
            "pending_syncs": self._pending_syncs.qsize(),
        }

    async def get_pending_syncs(self, limit: int = 100) -> List[Dict]:
        """Get pending sync operations"""
        syncs = []
        for _ in range(min(limit, self._pending_syncs.qsize())):
            try:
                sync = self._pending_syncs.get_nowait()
                syncs.append(sync)
            except asyncio.QueueEmpty:
                break
        return syncs


class DistributedActor:
    """Mixin for actors supporting distributed execution"""

    def __init__(self):
        self._device_id: Optional[str] = None
        self._region: Optional[str] = None
        self._distributed_coordinator: Optional[DistributedExecutionCoordinator] = None
        self._local_state: Dict[str, Any] = {}
        self._synced_at: Optional[datetime] = None

    def set_distributed_coordinator(self, coordinator: DistributedExecutionCoordinator) -> None:
        """Link actor to distributed coordinator"""
        self._distributed_coordinator = coordinator

    async def migrate_to_device(self, device_id: str) -> bool:
        """Migrate actor to different device"""
        if not self._distributed_coordinator:
            return False

        success = self._distributed_coordinator.place_actor_on_device(self.id, device_id)

        if success:
            self._device_id = device_id
            device = self._distributed_coordinator._devices.get(device_id)
            if device:
                self._region = device.region

        return success

    async def migrate_to_region(self, region: str) -> bool:
        """Migrate actor to different region"""
        if not self._distributed_coordinator:
            return False

        return self._distributed_coordinator.place_actor_in_region(self.id, region)

    def get_device_location(self) -> Optional[str]:
        """Get current device location"""
        if not self._distributed_coordinator:
            return None
        return self._distributed_coordinator.get_actor_location(self.id)

    def queue_local_event(self, event: Dict) -> None:
        """Queue event for sync to central"""
        if not self._distributed_coordinator or not self._device_id:
            return

        device = self._distributed_coordinator._devices.get(self._device_id)
        if device:
            device.queue_event(event)

    def queue_local_observation(self, observation: Dict) -> None:
        """Queue observation for sync"""
        if not self._distributed_coordinator or not self._device_id:
            return

        device = self._distributed_coordinator._devices.get(self._device_id)
        if device:
            device.queue_observation(observation)

    async def sync_to_central(self) -> bool:
        """Explicitly sync this actor's state to central"""
        if not self._distributed_coordinator or not self._device_id:
            return False

        success = await self._distributed_coordinator.sync_device_to_central(self._device_id)

        if success:
            self._synced_at = datetime.now()

        return success

    def is_on_edge(self) -> bool:
        """Check if executing on edge device"""
        if not self._device_id or not self._distributed_coordinator:
            return False

        device = self._distributed_coordinator._devices.get(self._device_id)
        return device and device.device_type == DeviceType.EDGE

    def get_distribution_stats(self) -> Dict[str, Any]:
        """Get distribution statistics"""
        return {
            "actor_id": self.id,
            "device_id": self._device_id,
            "region": self._region,
            "is_edge": self.is_on_edge(),
            "last_sync": self._synced_at,
        }
