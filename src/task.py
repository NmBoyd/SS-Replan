from __future__ import print_function

import numpy as np
import random
import time

from pybullet_tools.pr2_utils import get_viewcone
from pybullet_tools.utils import stable_z, link_from_name, set_pose, Pose, Point, Euler, multiply, get_pose, \
    apply_alpha, RED, step_simulation, joint_from_name, set_all_static, \
    WorldSaver, stable_z_on_aabb, wait_for_user, draw_aabb, get_aabb, pairwise_collision, elapsed_time, set_base_values
from src.stream import get_stable_gen
from src.utils import BLOCK_SIZES, BLOCK_COLORS, get_block_path, JOINT_TEMPLATE, COUNTERS, \
    get_ycb_obj_path, DRAWER_JOINTS, ALL_JOINTS, LEFT_CAMERA, KINECT_DEPTH, \
    KITCHEN_FROM_ZED_LEFT, CAMERA_MATRIX, CAMERA_POSES, CAMERAS, compute_surface_aabb, ZED_LEFT_SURFACES
from examples.discrete_belief.dist import DDist, UniformDist, DeltaDist
#from examples.pybullet.pr2_belief.problems import BeliefState, BeliefTask, OTHER
from src.belief import Belief, create_surface_belief

class Task(object):
    def __init__(self, world, prior={}, skeletons=[],
                 movable_base=True, noisy_base=True,
                 return_init_bq=True, return_init_aq=True,
                 goal_hand_empty=False, goal_holding=[], goal_detected=[],
                 goal_on={}, goal_open=[], goal_closed=[], goal_cooked=[],
                 init=[], goal=[]):
        self.world = world
        world.task = self
        self.prior = dict(prior) # DiscreteDist over
        self.skeletons = list(skeletons)
        self.movable_base = movable_base
        self.noisy_base = noisy_base
        self.return_init_bq = return_init_bq
        self.return_init_aq = return_init_aq
        self.goal_hand_empty = goal_hand_empty
        self.goal_holding = set(goal_holding)
        self.goal_on = dict(goal_on)
        self.goal_detected = set(goal_detected)
        self.goal_open = set(goal_open)
        self.goal_closed = set(goal_closed)
        self.goal_cooked = set(goal_cooked)
        self.init = init
        self.goal = goal
    @property
    def objects(self):
        return set(self.prior.keys())
    def create_belief(self):
        t0 = time.time()
        print('Creating initial belief')
        belief = create_surface_belief(self.world, self.prior)
        belief.task = self
        print('Took {:2f} seconds'.format(elapsed_time(t0)))
        return belief
    def __repr__(self):
        return '{}{}'.format(self.__class__.__name__, {
            key: value for key, value in self.__dict__.items() if value not in [self.world]})

################################################################################

# (x, y, yaw)
UNIT_POSE2D = (0., 0., 0.)
BOX_POSE2D = (0.1, 1.15, 0.)
SPAM_POSE2D = (0.125, 1.175, -np.pi / 4)
CRACKER_POSE2D = (0.2, 1.2, np.pi/4)

def pose2d_on_surface(world, entity_name, surface_name, pose2d=UNIT_POSE2D):
    x, y, yaw = pose2d
    body = world.get_body(entity_name)
    surface_aabb = compute_surface_aabb(world, surface_name)
    z = stable_z_on_aabb(body, surface_aabb)
    pose = Pose(Point(x, y, z), Euler(yaw=yaw))
    set_pose(body, pose)
    return pose

def add_block(world, idx=0, **kwargs):
    # TODO: automatically produce a unique name
    name = '{}_{}_block{}'.format(BLOCK_SIZES[-1], BLOCK_COLORS[0], idx)
    entity_path = get_block_path(name)
    #name = 'potted_meat_can'
    #entity_path = get_ycb_obj_path(name)
    world.add_body(name, entity_path)
    pose2d_on_surface(world, name, COUNTERS[0], **kwargs)
    return name

def add_box(world, idx=0, **kwargs):
    ycb_type = 'cracker_box'
    name = '{}{}'.format(ycb_type, idx)
    obstruction_path = get_ycb_obj_path(ycb_type)
    world.add_body(name, obstruction_path, color=np.ones(4))
    pose2d_on_surface(world, name, COUNTERS[0], **kwargs)
    return name

def add_kinect(world, camera_name=LEFT_CAMERA):
    # TODO: could intersect convex with half plane
    world_from_camera = multiply(get_pose(world.kitchen), CAMERA_POSES[camera_name])
    world.add_camera(camera_name, world_from_camera, CAMERA_MATRIX)

################################################################################

def sample_placement(world, entity_name, surface_name, **kwargs):
    entity_body = world.get_body(entity_name)
    placement_gen = get_stable_gen(world, pos_scale=1e-3, rot_scale=1e-2, **kwargs)
    with WorldSaver():
        for pose, in placement_gen(entity_name, surface_name):
            pose.assign()
            if not any(pairwise_collision(entity_body, obst_body) for obst_body in
                       world.body_from_name.values() if entity_body != obst_body):
                break
    pose.assign()
    return pose

def close_all_doors(world):
    for joint in world.kitchen_joints:
        world.close_door(joint)

def open_all_doors(world):
    for joint in world.kitchen_joints:
        world.open_door(joint)

################################################################################

def detect_block(world, **kwargs):
    entity_name = add_block(world, idx=0, pose2d=BOX_POSE2D)
    obstruction_name = add_box(world, idx=0, pose2d=CRACKER_POSE2D)
    #other_name = add_box(world, idx=1)
    set_all_static()
    for side in CAMERAS[:1]:
        add_kinect(world, side)
    initial_distribution = UniformDist(['indigo_drawer_top']) # indigo_tmp
    initial_surface = initial_distribution.sample()
    if random.random() < 0.:
        # TODO: sometimes base/arm failure causes the planner to freeze
        # Freezing is because the planner is struggling to find new samples
        sample_placement(world, entity_name, initial_surface, learned=True)
    #sample_placement(world, other_name, 'hitman_tmp', learned=True)

    prior = {
        entity_name: UniformDist(['indigo_tmp']),  # 'indigo_drawer_top'
        obstruction_name: DeltaDist('indigo_tmp'),
        # TODO: test multiple rays on the object itself from the viewcone of th ething
    }
    return Task(world, prior=prior, movable_base=True,
                return_init_bq=True, return_init_aq=True,
                #goal_detected=[entity_name],
                #goal_holding=[entity_name],
                goal_on={entity_name: 'indigo_drawer_top'},
                goal_closed=ALL_JOINTS,
                **kwargs)

################################################################################

def hold_block(world, **kwargs):
    #open_all_doors(world)
    entity_name = add_block(world, idx=0, pose2d=SPAM_POSE2D)
    initial_surface = 'indigo_tmp' # hitman_tmp | indigo_tmp
    set_all_static()
    add_kinect(world)
    sample_placement(world, entity_name, initial_surface, learned=True)
    prior = {
        entity_name: DeltaDist(initial_surface),
    }
    return Task(world, prior=prior, movable_base=True,
                return_init_bq=True, # return_init_aq=False,
                goal_holding=[entity_name],
                #goal_closed=ALL_JOINTS,
                **kwargs)


################################################################################

BASE_POSE2D = (0.73, 0.80, -np.pi)


def fixed_stow(world, **kwargs):
    # set_base_values
    entity_name = add_block(world, idx=0, pose2d=SPAM_POSE2D)
    set_all_static()
    add_kinect(world)

    # set_base_values(world.robot, BASE_POSE2D)
    world.set_base_conf(BASE_POSE2D)

    initial_surface = 'indigo_tmp'
    goal_surface = 'indigo_drawer_top'
    sample_placement(world, entity_name, initial_surface, learned=True)
    # joint_name = JOINT_TEMPLATE.format(goal_surface)

    prior = {
        entity_name: DeltaDist(initial_surface),
    }
    return Task(world, prior=prior, movable_base=False,
                goal_on={entity_name: goal_surface},
                return_init_bq=True, return_init_aq=True,
                goal_closed=ALL_JOINTS,
                **kwargs)

################################################################################

def stow_block(world, **kwargs):
    #world.open_gq.assign()
    # dump_link_cross_sections(world, link_name='indigo_drawer_top')
    # wait_for_user()

    entity_name = add_block(world, idx=0, pose2d=SPAM_POSE2D)
    #entity_name = add_block(world, x=0.2, y=1.15, idx=1) # Will be randomized anyways
    #obstruction_name = add_box(world, idx=0)
    # test_grasps(world, entity_name)
    set_all_static()
    add_kinect(world)  # TODO: this needs to be after set_all_static

    #initial_surface = random.choice(DRAWERS) # COUNTERS | DRAWERS | SURFACES | CABINETS
    initial_surface = 'indigo_tmp' # hitman_tmp | indigo_tmp | range
    #initial_surface = 'indigo_drawer_top'
    goal_surface = 'indigo_drawer_top' # baker | hitman_drawer_top | indigo_drawer_top | hitman_tmp | indigo_tmp
    print('Initial surface: | Goal surface: ', initial_surface, initial_surface)
    sample_placement(world, entity_name, initial_surface, learned=True)
    #sample_placement(world, obstruction_name, 'hitman_tmp')

    joint_name = 'indigo_drawer_top_joint'
    #world.open_door(joint_from_name(world.kitchen, joint_name))

    #initial_surface = 'golf' # range | table | golf
    #surface_body = world.environment_bodies[initial_surface]
    #draw_aabb(get_aabb(surface_body))
    #while True:
    #    sample_placement(world, entity_name, surface_name=initial_surface, learned=False)
    #    wait_for_user()

    prior = {
        entity_name: DeltaDist(initial_surface),
    }
    return Task(world, prior=prior, movable_base=True,
                #goal_holding=[entity_name],
                goal_on={entity_name: goal_surface},
                return_init_bq=True, return_init_aq=True,
                #goal_open=[joint_name],
                goal_closed=ALL_JOINTS,
                **kwargs)

################################################################################

TASKS = [
    detect_block,
    hold_block,
    fixed_stow,
    stow_block,
]
