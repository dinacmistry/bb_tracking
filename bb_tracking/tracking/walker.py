"""Walkers provide a simple framework for walking through the beesbook data.

It will provide detections, manage tracks, assign detections and close tracks.

You basically *just* have to provide a scoring function. Examples and helpers to generate scoring
functions are provided in :mod:`.training`.
"""
# pylint:disable=too-many-arguments,too-few-public-methods
import copy
import numpy as np
from scipy.optimize import linear_sum_assignment
from ..data import DataWrapperTracks, Detection, Track
from ..data.constants import DETKEY


class SimpleWalker(object):
    """Class for walking through the beesbook data."""
    data = None
    """:obj:`.DataWrapper`: the `DataWrapper` object to access detections"""
    score_fun = None
    """func: scoring function to calculate the weights between two frame objects"""
    frame_diff = None
    """int: after n frames a track is closed if no matching frame object is found"""
    radius = None
    """int: radius in image coordinates to restrict neighborhood search"""
    track_id_count = 0
    """int: counter to increment unique track ids"""
    max_weight = 1000.
    """float: used to mark non assignable pairs in a cost matrix"""
    prune_weight = 1000.
    """float: used to ignore claims with bad weights"""
    min_track_start_length = 1
    """int: minimum length of track to start a new track as base of a path.
    Only relevant when assigning :obj:`.Track` to other :obj:`Track` objects."""
    assigned_tracks = None
    """(:obj:`set`): keeps track of all the tracks that are already assigned"""
    track_prefix = None
    """str: prefix for :attr:`.Track.id` for unique track ids over several instances"""

    def __init__(self, data_wrapper, score_fun, frame_diff, radius, track_prefix=None):
        """Initialization of a simple Walker to calculate tracks.

        This Walker will run through the data time step for time step, no jumps or sliding windows,
        hence the naming.

        The Walker could be used multiple times but will share the `track_id_count` to
        generate unique :obj:`.Track` ids.

        Arguments:
            data_wrapper (:obj:`.DataWrapper`): a :obj:`.DataWrapper` object to access frame objects
            score_fun (func): scoring function to calculate the weights between two frame objects
            frame_diff (int): after n frames a close track if no matching object is found
            radius (int): radius in image coordinates to restrict neighborhood search

        Keyword Argument:
            track_prefix (Optional str): prefix for :attr:`.Track.id` for unique track ids
        """
        self.data = data_wrapper
        self.score_fun = score_fun
        self.frame_diff = frame_diff
        self.radius = radius
        self.track_prefix = track_prefix
        self.assigned_tracks = set()

    def calc_tracks(self, start=None, stop=None):
        """Merge frame objects to bigger :obj:`.Track` objects.

        Note:
            At the moment this walker only considers the frame objects on each camera separately.
            This will have to be adjusted once the stitching is completed.

        Keyword Arguments:
            start (tstamp): restrict to frames with tstamp >= start
            stop (tstamp): restrict to frames with tstamp < stop

        Returns:
            :obj:`list` of :obj:`.Track`: :obj:`list` of merged :obj:`.Track`
        """
        if isinstance(self.data, DataWrapperTracks):
            calc_timestep = self._calc_timestep_tracks
        else:
            calc_timestep = self._calc_timestep_detections

        closed_tracks = []
        for cam_id in self.data.get_camids():
            tstamps = self.data.get_timestamps(cam_id=cam_id)
            waiting = []
            for time_idx, tstamp in enumerate(tstamps):
                # not within time range (yet)
                if start is not None and tstamp < start:
                    continue

                # out of time range (now)
                if stop is not None and tstamp >= stop:
                    break

                waiting = calc_timestep(cam_id, time_idx, tstamp, tstamps, waiting, closed_tracks)

            # close remaining tracks
            for _, waiting_track in waiting:
                closed_tracks.append(waiting_track)
        return closed_tracks

    def _calc_timestep_detections(self, cam_id, time_idx, tstamp, tstamps, waiting, closed_tracks):
        """Assigns detections of type :obj:`Detection` to tracks :obj:`Track` in the waiting list.

        Arguments:
            cam_id (int): the cam to consider
            time_idx (int): current time index in `tstamps`
            tstamp (tstamp): the current timestamp
            tstamps (list of timestamps): list of all available timestamps for walker
            waiting (:obj:`list` of :obj:`.Track`): the waiting list with tracks to be extended
                or closed
            closed_tracks (:obj:`list` of :obj:`.Track`): list with closed tracks

        Returns:
            list of obj:`.Track`: new waiting list with new or extended tracks
        """
        frame_objects = self.data.get_frame_objects(cam_id=cam_id, timestamp=tstamp)
        # no waiting tracks so load all from current frame
        if not waiting:
            waiting = self._calc_initialize(time_idx, tstamps, frame_objects, waiting)
            return waiting
        # close track
        waiting = self._calc_close_tracks(time_idx, waiting, closed_tracks)
        # assign frame objects to tracks
        waiting, assigned = self._calc_assign(cam_id, time_idx, tstamp, tstamps,
                                              frame_objects, waiting)
        # add unclaimed frame objects as new tracks
        unassigned = set([obj.id for obj in frame_objects]) - assigned
        return self._calc_initialize(time_idx, tstamps,
                                     self.data.get_detections(unassigned), waiting)

    def _calc_timestep_tracks(self, cam_id, time_idx, tstamp, tstamps, waiting, closed_tracks):
        """Assigns tracks of type :obj:`.Track` to other tracks of type :obj:`.Track`
        in the waiting list.

        Arguments:
            cam_id (int): the cam to consider
            time_idx (int): current time index in `tstamps`
            tstamp (tstamp): the current tstamp
            tstamps (list of timestamps): list of all available timestamps for walker
            waiting (:obj:`list` of :obj:`.Track`): the waiting list with tracks to be extended
                or closed
            closed_tracks (:obj:`list` of :obj:`.Track`): list with closed tracks

        Returns:
            :obj:`list` of obj:`.Track`: new waiting list with new or extended tracks
        """
        fot = self.data.get_frame_objects_starting(cam_id=cam_id, timestamp=tstamp)
        # no waiting tracks so load all from current frame
        if not waiting:
            waiting = self._calc_initialize(time_idx, tstamps, fot, waiting)
            self.assigned_tracks |= set([frame_object.id for frame_object in fot])
            return waiting
        # close track
        waiting = self._calc_close_tracks(time_idx, waiting, closed_tracks)
        # assign frame objects to tracks
        waiting, assigned = self._calc_assign(cam_id, time_idx, tstamp, tstamps, fot, waiting)
        # add unclaimed frame objects as new tracks
        all_ids = set([frame_object.id for frame_object in fot])
        self.assigned_tracks |= all_ids
        unassigned = all_ids - assigned
        return self._calc_initialize(time_idx, tstamps, self.data.get_tracks(unassigned), waiting)

    def _calc_initialize(self, time_idx, tstamps, frame_objects, waiting):
        """Initializes the waiting list with Tracks.

        Arguments:
            time_idx (int): current time index in `tstamps`
            tstamps (list of timestamps): list of all available timestamps for walker
            frame_objects (list of :obj:`.Detection` or :obj:`.Track`): the frame objects associated
                with the current frame
            waiting (list of :obj:`.Track`): the waiting list with tracks to be extended or closed

        Returns:
            waiting (list of :obj:`.Track`): new waiting list initialized with Tracks
        """
        if len(frame_objects) == 0:
            return waiting
        object_type = type(frame_objects[0])
        if object_type is Detection:
            for frame_object in frame_objects:
                if self.track_prefix is None:
                    track_id = self.track_id_count
                else:
                    track_id = "".join([self.track_prefix, str(self.track_id_count)])
                waiting.append([time_idx, Track(id=track_id,
                                                ids=[frame_object.id],
                                                timestamps=[frame_object.timestamp],
                                                meta={DETKEY: [frame_object, ]})])
                self.track_id_count += 1
        elif object_type is Track:
            for frame_object in frame_objects:
                if len(frame_object.ids) >= self.min_track_start_length:
                    if self.track_prefix is None:
                        track_id = self.track_id_count
                    else:
                        track_id = "".join([self.track_prefix, str(self.track_id_count)])
                    track = copy.deepcopy(frame_object)
                    # get offset because a track has a length
                    new_time_idx = np.where(np.array(tstamps) == track.timestamps[-1])[0][0]
                    waiting.append([new_time_idx, track._replace(id=track_id)])
                    self.track_id_count += 1
        else:
            raise TypeError("Type {0} not supported.".format(object_type))

        return waiting

    def _calc_close_tracks(self, time_idx, waiting, closed_tracks):
        """Closes tracks in the waiting list that can/should not be extended.

        Arguments:
            time_idx (int): current time index in `tstamps`
            waiting (list of :obj:`.Track`): the waiting list with tracks to be extended or closed
            closed_tracks (list of :obj:`.Track`): list with closed tracks

        Returns:
            waiting (list of :obj:`.Track`): the waiting list with tracks to be extended or closed
        """
        new_waiting = []
        for time_last_add, waiting_track in waiting:
            if (time_idx - time_last_add) > self.frame_diff:
                closed_tracks.append(waiting_track)
            else:
                new_waiting.append([time_last_add, waiting_track])
        return new_waiting

    def _calc_assign(self, cam_id, time_idx, tstamp, tstamps, frame_objects, waiting):
        """Assigns frame objects to tracks in the waiting list.

        Arguments:
            cam_id (int): the cam to consider
            time_idx (int): current time index in `tstamps`
            tstamp (tstamp): the current tstamp
            tstamps (list of timestamps): list of all available timestamps for walker
            frame_objects (list of :obj:`.Detection` or :obj:`.Track`): the frame objects associated
                with the current frame
            waiting (list of :obj:`.Track`): the waiting list with tracks to be extended or closed

        Returns:
            tuple: tuple containing:
                - **waiting** (:obj:`.Track`): new waiting list with new or extended tracks
                - **assigned** (:obj:`set` of ids): set with assigned detection ids
        """
        # pylint:disable=too-many-locals
        if len(frame_objects) == 0:
            return waiting, set()
        if not isinstance(frame_objects[0], (Detection, Track)):
            raise TypeError("Type {0} not supported.".format(type(frame_objects[0])))
        # make claims
        cost_matrix = self._calc_make_claims(cam_id, time_idx, tstamp, frame_objects, waiting)
        # resolve claims
        assigned = set()
        rows, cols = self._resolve_claims(cost_matrix)
        # append assigned frame objects to tracks
        for waiting_idx, fo_idx in zip(rows, cols):
            # do not add if cost is too high
            if cost_matrix[waiting_idx, fo_idx] >= self.max_weight:
                continue
            waiting[waiting_idx][0] = time_idx

            frame_object = frame_objects[fo_idx]
            assigned.add(frame_object.id)
            track = waiting[waiting_idx][1]
            if isinstance(frame_object, Detection):
                track.ids.append(frame_object.id)
                track.timestamps.append(tstamp)
                track.meta[DETKEY].append(frame_object)
            elif isinstance(frame_object, Track):
                # get offset because a track has a length and we won't see it again for some time
                new_time_idx = np.where(np.array(tstamps) == frame_object.timestamps[-1])[0][0]

                waiting[waiting_idx][0] = new_time_idx
                track.ids.extend(frame_object.ids)
                track.timestamps.extend(frame_object.timestamps)
                for key in track.meta.keys():
                    if key not in frame_object.meta.keys():
                        continue
                    if isinstance(frame_object.meta[key], list):
                        track.meta[key].extend(frame_object.meta[key])
            else:  # pragma: no cover
                raise TypeError("Type {0} not supported.".format(type(frame_object)))

        return waiting, assigned

    def _calc_make_claims(self, cam_id, time_idx, tstamp, frame_objects, waiting):
        """Make claims for tracks in waiting list.

        Arguments:
            cam_id (int): the cam to consider
            time_idx (int): current time index in `tstamps`
            tstamp (tstamp): the current tstamp
            frame_objects (list of :obj:`.Detection` or :obj:`.Track`): the frame objects associated
                with the current frame
            waiting (list of :obj:`.Track`): the waiting list with tracks to be extended or closed

        Returns:
            cost_matrix (:obj:`np.array`): Matrix with waiting (row)
                to frame object (col) assignment weight
        """
        # pylint:disable=too-many-locals
        fo_index_rev = {fo.id: value for value, fo in enumerate(frame_objects)}

        waiting_indices, fo_indices, tracks_path, fo_test = [], [], [], []
        for i, (track_time_idx, track_path) in enumerate(waiting):
            if track_time_idx >= time_idx:
                continue
            neighbors = self.data.get_neighbors(track_path, cam_id,
                                                radius=self.radius, timestamp=tstamp)
            if not neighbors:
                continue

            waiting_indices.extend([i] * len(neighbors))
            fo_indices.extend([fo_index_rev[fo.id] for fo in neighbors])
            tracks_path.extend([track_path] * len(neighbors))
            fo_test.extend(neighbors)
        cost_matrix = np.full((len(waiting), len(fo_index_rev)), self.max_weight)
        if len(fo_test) > 0:
            cost_matrix[waiting_indices, fo_indices] = self.score_fun(tracks_path, fo_test)
        return cost_matrix

    def _resolve_claims(self, cost_matrix):
        """Resolves claims in cost_matrix.

        Arguments:
            cost_matrix (:obj:`np.array`): represents costs to assign a detection (cols)
                to a track (rows)

        Returns:
            rows (:obj:`np.array`): Row Indices that are assigned to columns
            cols (:obj:`np.array`): Col Indices that are assigned to rows in same order
        """
        cost_matrix[cost_matrix >= self.prune_weight] = self.max_weight
        return linear_sum_assignment(cost_matrix)
