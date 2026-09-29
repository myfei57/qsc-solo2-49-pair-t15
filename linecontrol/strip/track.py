"""Seam position tracking and its snapshot."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from ..domain.records import TrackSnapshot, utcnow
from ..store.keys import KEY_TRACK
from ..store.snapshot import save_json


class TrackOps:
    """Mixed into :class:`~linecontrol.strip.strip.Strip`."""

    def track_update(self, position: float) -> TrackSnapshot:
        self.data.track = float(position)
        self.data.seam_sheared = False
        return self._persist()

    def track_refresh(self, position: float, seam_id: str) -> TrackSnapshot:
        """Move the track point onto a freshly generated seam."""

        self.data.track = float(position)
        self.data.seam_id = seam_id
        self.data.seam_sheared = False
        snapshot = self._persist()
        self.bus.publish(
            Event(
                kind=EventKind.TRACK,
                source="strip",
                message=seam_id,
                payload=float(position),
            )
        )
        return snapshot

    def track_position(self) -> float:
        return self.data.track

    def seam_id(self) -> str:
        return self.data.seam_id

    def shear_permit(self) -> bool:
        return self.data.seam_id != "" and self.data.track > 0

    def seam_sheared(self) -> bool:
        """Report whether the tracked seam has already been cut."""

        return self.data.seam_sheared

    def mark_sheared(self) -> TrackSnapshot:
        """Record that the shear cut the tracked seam."""

        self.data.seam_sheared = True
        return self._persist()

    def restore_track(self, snapshot: TrackSnapshot) -> None:
        self.data.track = snapshot.position
        self.data.seam_id = snapshot.seam_id
        self.data.seam_sheared = snapshot.sheared

    def _persist(self) -> TrackSnapshot:
        snapshot = TrackSnapshot(
            position=self.data.track,
            seam_id=self.data.seam_id,
            saved=utcnow(),
            sheared=self.data.seam_sheared,
        )
        save_json(self.store, KEY_TRACK, snapshot)
        return snapshot
