#!/usr/bin/env python3
# <mal@malm.co.uk>
# 21.11.21
#
# my-tree.py
# ----------
# RGB Xmas tree from Pi Hut.  See here for details:
# https://thepihut.com/products/3d-rgb-xmas-tree-for-raspberry-pi
# Use PiHut's rgbxmastree tree module here:
# https://github.com/ThePiHut/rgbxmastree
# Download the tree module to your local directory as follows:
# wget https://bit.ly/2Lr9CT3 -O tree.py
# To ensure this script runs at boot add the following line to /etc/rc.local:
# python3 /home/pi/raspberrypi-xmas/my-tree.py
# assuming you git cloned this repo to your home directory
#
# This script builds on PiHut's huecycle.py script.
# It cycles three groups of tree lights through 10 degrees of hue
# and leaves the top light flashing white.
#

from tree import RGBXmasTree
from colorzero import Color, Hue
from time import sleep
from random import random

# LED number for star at the top of the tree
STAR = 3
LED_SET = [list(range(25)[::3]), list(range(25)[1::3]), list(range(25)[2::3])]

# Create an instance of an RGBXmasTree
tree = RGBXmasTree(brightness=0.1)
colors = [Color('red'),Color('green'),Color('blue')]
# Initialise the LEDs to starting colours
for i, leds in enumerate(LED_SET):
    for led in leds:
        tree[led].color = colors[i]
# Colour top LED white (will flash due to loop)
tree[STAR].color = Color('white')
# Main loop - hue phase in a slow cycle
try:
    while True:
        for leds in LED_SET:
            for led in leds:
                tree[led].color += Hue(deg=10)
        tree[STAR].color = Color('black')
        #sleep(0.01)
        tree[STAR].color = Color('white')
except KeyboardInterrupt:
    tree.close()


