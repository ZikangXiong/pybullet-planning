from __future__ import print_function

import argparse
import random
import numpy as np
import time
import math
from collections import OrderedDict, defaultdict
from itertools import combinations

from pybullet_tools.utils import load_model, TURTLEBOT_URDF, joints_from_names, \
    set_joint_positions, HideOutput, get_bodies, sample_placement, pairwise_collision, \
    set_point, Point, create_box, stable_z, TAN, GREY, connect, PI, OrderedSet, \
    wait_if_gui, dump_body, set_all_color, BLUE, child_link_from_joint, link_from_name, draw_pose, Pose, pose_from_pose2d, \
    get_random_seed, get_numpy_seed, set_random_seed, set_numpy_seed, plan_joint_motion, plan_nonholonomic_motion, \
    joint_from_name, safe_zip, draw_base_limits, BodySaver, WorldSaver, LockRenderer, elapsed_time, disconnect, flatten, \
    INF, wait_for_duration, get_unbuffered_aabb, draw_aabb, DEFAULT_AABB_BUFFER, get_link_pose, get_joint_positions, \
    get_subtree_aabb, get_pairs, get_distance_fn, get_aabb, set_all_static, step_simulation, get_bodies_in_region, \
    AABB, Profiler, pairwise_link_collision, BASE_LINK, get_collision_data, draw_pose2d, \
    normalize_interval, wrap_angle, CIRCULAR_LIMITS, wrap_interval, Euler, rescale_interval, adjust_path, \
    contact_collision, timer, update_scene, set_aabb_buffer, set_separating_axis_collisions, get_aabb, set_pose, \
    Pose, get_all_links, can_collide, aabb_overlap, set_collision_pair_mask, randomize, DEFAULT_RESOLUTION, base_aligned_z

BASE_LINK_NAME = 'base_link'
BASE_JOINTS = ['x', 'y', 'theta']
DRAW_Z = 1e-3
DRAW_LENGTH = 0.5
MIN_AABB_VOLUME = DEFAULT_AABB_BUFFER**3

##################################################

def create_custom_base_limits(robot, base_limits):
    return {joint_from_name(robot, joint): limits
            for joint, limits in safe_zip(BASE_JOINTS[:2], zip(*base_limits))}

def sample_placements(body_surfaces, obstacles=None, savers=[], min_distances={}):
    if obstacles is None:
        obstacles = set(get_bodies()) - set(body_surfaces)
    savers = list(savers) + [BodySaver(obstacle) for obstacle in obstacles]
    if not isinstance(min_distances, dict):
        min_distances = {body: min_distances for body in body_surfaces}
    # TODO: max attempts here
    for body, surface in body_surfaces.items(): # TODO: shuffle
        min_distance = min_distances.get(body, 0.)
        while True:
            pose = sample_placement(body, surface)
            if pose is None:
                for saver in savers:
                    saver.restore()
                return False
            for saver in savers:
                obstacle = saver.body
                if obstacle in [body, surface]:
                    continue
                saver.restore()
                if pairwise_collision(body, obstacle, max_distance=min_distance):
                    break
            else:
                savers.append(BodySaver(body))
                break
    for saver in savers:
        saver.restore()
    return True

def draw_path(path2d, z=DRAW_Z, **kwargs):
    if path2d is None:
        return []
    #return list(flatten(draw_pose(pose_from_pose2d(pose2d, z=z), **kwargs) for pose2d in path2d))
    #return list(flatten(draw_pose2d(pose2d, z=z, **kwargs) for pose2d in path2d))
    base_z = 1.
    start = path2d[0]
    mid_yaw = start[2]
    #mid_yaw = wrap_interval(mid_yaw)
    interval = (mid_yaw - PI, mid_yaw + PI)
    #interval = CIRCULAR_LIMITS
    draw_pose(pose_from_pose2d(start, z=base_z), length=1, **kwargs)
    # TODO: draw the current pose
    # TODO: line between orientations when there is a jump
    return list(flatten(draw_pose2d(pose2d, z=base_z+rescale_interval(
        wrap_interval(pose2d[2], interval=interval), old_interval=interval, new_interval=(-0.5, 0.5)), **kwargs)
                        for pose2d in path2d))


def plan_motion(robot, joints, goal_positions, attachments=[], obstacles=None, holonomic=False, reversible=False, **kwargs):
    attached_bodies = [attachment.child for attachment in attachments]
    moving_bodies = [robot] + attached_bodies
    if obstacles is None:
        obstacles = get_bodies()
    obstacles = set(obstacles) - set(moving_bodies)
    if holonomic:
        return plan_joint_motion(robot, joints, goal_positions,
                                 attachments=attachments, obstacles=obstacles, **kwargs)
    # TODO: just sample the x, y waypoint and use the resulting orientation
    # TODO: remove overlapping configurations/intervals due to circular joints
    return plan_nonholonomic_motion(robot, joints, goal_positions, reversible=reversible,
                                    linear_tol=1e-6, angular_tol=0.,
                                    attachments=attachments, obstacles=obstacles, **kwargs)

##################################################

def problem1(n_obstacles=10, wall_side=0.1, obst_width=0.25, obst_height=0.5):
    floor_extent = 5.0
    base_limits = (-floor_extent/2.*np.ones(2),
                   +floor_extent/2.*np.ones(2))

    floor_height = 0.001
    floor = create_box(floor_extent, floor_extent, floor_height, color=TAN)
    set_point(floor, Point(z=-floor_height/2.))

    wall1 = create_box(floor_extent + wall_side, wall_side, wall_side, color=GREY)
    set_point(wall1, Point(y=floor_extent/2., z=wall_side/2.))
    wall2 = create_box(floor_extent + wall_side, wall_side, wall_side, color=GREY)
    set_point(wall2, Point(y=-floor_extent/2., z=wall_side/2.))
    wall3 = create_box(wall_side, floor_extent + wall_side, wall_side, color=GREY)
    set_point(wall3, Point(x=floor_extent/2., z=wall_side/2.))
    wall4 = create_box(wall_side, floor_extent + wall_side, wall_side, color=GREY)
    set_point(wall4, Point(x=-floor_extent/2., z=wall_side/2.))
    walls = [wall1, wall2, wall3, wall4]

    initial_surfaces = OrderedDict()
    for _ in range(n_obstacles):
        body = create_box(obst_width, obst_width, obst_height, color=GREY)
        initial_surfaces[body] = floor
    pillars = list(initial_surfaces)
    obstacles = walls + pillars

    initial_conf = np.array([+floor_extent/3, -floor_extent/3, 3*PI/4])
    goal_conf = -initial_conf

    with HideOutput():
        robot = load_model(TURTLEBOT_URDF, merge=True, sat=False)
        base_joints = joints_from_names(robot, BASE_JOINTS)
        # base_link = child_link_from_joint(base_joints[-1])
        base_link = link_from_name(robot, BASE_LINK_NAME)
        set_all_color(robot, BLUE)
    dump_body(robot)
    set_point(robot, Point(z=stable_z(robot, floor)))
    #set_point(robot, Point(z=base_aligned_z(robot)))
    draw_pose(Pose(), parent=robot, parent_link=base_link, length=0.5)
    set_joint_positions(robot, base_joints, initial_conf)

    sample_placements(initial_surfaces, obstacles=[robot] + walls,
                      savers=[BodySaver(robot, joints=base_joints, positions=goal_conf)],
                      min_distances=10e-2)

    # The first calls appear to be the slowest
    # times = []
    # for body1, body2 in combinations(pillars, r=2):
    #     start_time = time.time()
    #     colliding = pairwise_collision(body1, body2)
    #     runtime = elapsed_time(start_time)
    #     print(colliding, runtime)
    #     times.append(runtime)
    # print(times)

    return robot, base_limits, goal_conf, obstacles

##################################################

def compute_cost(robot, joints, path, resolutions=None):
    if path is None:
        return INF
    return sum(get_distance_fn(robot, joints, weights=resolutions)(*pair)
               for pair in get_pairs(path))

def iterate_path(robot, joints, path, step_size=None): # 1e-2 | None
    if path is None:
        return
    path = adjust_path(robot, joints, path)
    with LockRenderer():
        handles = draw_path(path)
    wait_if_gui(message='Begin?')
    for i, conf in enumerate(path):
        set_joint_positions(robot, joints, conf)
        if step_size is None:
            wait_if_gui(message='{}/{} Continue?'.format(i, len(path)))
        else:
            wait_for_duration(duration=step_size)
    wait_if_gui(message='Finish?')

##################################################

def test_aabb(robot):
    base_link = link_from_name(robot, BASE_LINK_NAME)
    region_aabb = AABB(lower=-np.ones(3), upper=+np.ones(3))
    draw_aabb(region_aabb)

    # bodies = get_bodies_in_region(region_aabb)
    # print(len(bodies), bodies)
    # for body in get_bodies():
    #     set_pose(body, Pose())

    #step_simulation()  # Need to call before get_bodies_in_region
    #update_scene()
    for i in range(3):
        with timer(message='{:f}'):
            bodies = get_bodies_in_region(region_aabb) # This does cache some info
        print(i, len(bodies), bodies)
    # https://github.com/bulletphysics/bullet3/search?q=broadphase
    # https://github.com/bulletphysics/bullet3/search?p=1&q=getCachedOverlappingObjects&type=&utf8=%E2%9C%93
    # https://andysomogyi.github.io/mechanica/bullet.html
    # http://www.cs.kent.edu/~ruttan/GameEngines/lectures/Bullet_User_Manual

    aabb = get_aabb(robot)
    # aabb = get_subtree_aabb(robot, base_link)
    print(aabb)
    draw_aabb(aabb)

    for link in [BASE_LINK, base_link]:
        print(link, get_collision_data(robot, link), pairwise_link_collision(robot, link, robot, link))

def test_caching(robot, obstacles):
    with timer(message='{:f}'):
        #update_scene() # 5.19752502441e-05
        step_simulation() # 0.000210046768188
    with timer(message='{:f}'):
        #print(get_aabb(robot, link=None, only_collision=True))
        print(contact_collision()) # 2.50339508057e-05
    for _ in range(3):
        with timer(message='{:f}'):
            #print(get_aabb(robot, link=None, only_collision=True)) # Recomputes each time
            print(contact_collision()) # 1.69277191162e-05

    print()
    obstacle = obstacles[-1]
    #for link in get_all_links(robot):
    #    set_collision_pair_mask(robot, obstacle, link1=link, enable=False) # Doesn't seem to affect pairwise_collision
    with timer('{:f}'):
        print(pairwise_collision(robot, obstacle)) # 0.000031
    links = get_all_links(robot)
    links = [link for link in get_all_links(robot) if can_collide(robot, link)]
    #links = randomize(links)
    with timer('{:f}'):
        print(any(pairwise_collision(robot, obstacle, link1=link) for link in links # 0.000179
                  ))
                  #if aabb_overlap(get_aabb(robot, link), get_aabb(obstacles[-1]))))
                  #if can_collide(robot, link)))
    with timer('{:f}'):
        print(pairwise_collision(robot, obstacle))

##################################################

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--cfree', action='store_true',
                        help='When enabled, disables collision checking.')
    # parser.add_argument('-p', '--problem', default='test_pour', choices=sorted(problem_fn_from_name),
    #                     help='The name of the problem to solve.')
    parser.add_argument('--holonomic', action='store_true', # '-h',
                        help='')
    parser.add_argument('-l', '--lock', action='store_false',
                        help='')
    parser.add_argument('-s', '--seed', default=None, type=int, # None | 1
                        help='The random seed to use.')
    parser.add_argument('-v', '--viewer', action='store_false',
                        help='')
    args = parser.parse_args()
    connect(use_gui=args.viewer)
    #set_aabb_buffer(buffer=1e-3)
    #set_separating_axis_collisions()

    seed = args.seed
    #seed = 0
    #seed = time.time()
    set_random_seed(seed=seed) # None: 2147483648 = 2**31
    set_numpy_seed(seed=seed)
    print('Random seed:', get_random_seed(), random.random())
    print('Numpy seed:', get_numpy_seed(), np.random.random())

    #########################

    robot, base_limits, goal_conf, obstacles = problem1()
    custom_limits = create_custom_base_limits(robot, base_limits)
    base_joints = joints_from_names(robot, BASE_JOINTS)

    draw_base_limits(base_limits)
    # draw_pose(get_link_pose(robot, base_link), length=0.5)
    start_conf = get_joint_positions(robot, base_joints)
    for conf in [start_conf, goal_conf]:
        draw_pose(pose_from_pose2d(conf, z=DRAW_Z), length=DRAW_LENGTH)

    if args.cfree:
        obstacles = []
    # for obstacle in obstacles:
    #     draw_aabb(get_aabb(obstacle)) # Updates automatically
    #resolutions = None
    #resolutions = np.array([0.05, 0.05, math.radians(10)])
    resolutions = 1.0*DEFAULT_RESOLUTION*np.ones(len(base_joints))
    set_all_static() # Doesn't seem to affect

    test_aabb(robot)
    test_caching(robot, obstacles)
    #return

    #########################

    saver = WorldSaver()
    start_time = time.time()
    profiler = Profiler(field='cumtime', num=50) # tottime | cumtime | None
    profiler.save()
    with LockRenderer(lock=args.lock):
        # TODO: draw the search tree
        path = plan_motion(robot, base_joints, goal_conf, holonomic=args.holonomic,
                           obstacles=obstacles, self_collisions=False,
                           custom_limits=custom_limits, resolutions=resolutions,
                           use_aabb=True, cache=True, max_distance=0.,
                           restarts=2, iterations=20, smooth=20) # 20 | None
        saver.restore()
    #wait_for_duration(duration=1e-3)
    profiler.restore()

    #########################

    solved = path is not None
    length = INF if path is None else len(path)
    cost = compute_cost(robot, base_joints, path, resolutions=resolutions)
    print('Solved: {} | Length: {} | Cost: {:.3f} | Runtime: {:.3f} sec'.format(
        solved, length, cost, elapsed_time(start_time)))
    if path is None:
        wait_if_gui()
        disconnect()
        return
    iterate_path(robot, base_joints, path, step_size=2e-2)
    disconnect()

if __name__ == '__main__':
    main()
