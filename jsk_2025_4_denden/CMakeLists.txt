cmake_minimum_required(VERSION 3.0.2)
project(jsk_2025_4_denden)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  sound_play
  )
catkin_package(
  CATKIN_DEPENDS rospy std_msgs sound_play
)

include_directories(
  ${catkin_INCLUDE_DIRS}
)
