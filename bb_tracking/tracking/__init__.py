# -*- coding: utf-8 -*-
"""This package contains code to track movement data from bees.

Note:
    Deprecated scoring functions are not imported here!
"""

from .scoring import bit_array_to_int_v, score_id_sim, score_id_sim_v,\
    score_id_sim_orientation, score_id_sim_orientation_v, \
    score_id_sim_rotating, score_id_sim_rotating_v, \
    distance_orientations, distance_orientations_v, distance_positions_v

from .tracking import make_detection_score_fun, make_track_score_fun
from .training import train_and_evaluate, train_bin_clf

from .walker import SimpleWalker

__all__ = ['bit_array_to_int_v', 'score_id_sim', 'score_id_sim_v', 'score_id_sim_orientation',
           'score_id_sim_orientation_v', 'score_id_sim_rotating', 'score_id_sim_rotating_v',
           'distance_orientations', 'distance_orientations_v', 'distance_positions_v',
           'train_and_evaluate', 'train_bin_clf',
           'make_detection_score_fun', 'make_track_score_fun', 'SimpleWalker']
