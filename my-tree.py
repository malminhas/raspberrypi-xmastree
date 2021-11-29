#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# my-tree.py
# ----------------
# (c) 2021 Mal Minhas, <mal@malm.co.uk>
#
# my-tree.py
# ----------------
# Simple disco lights pattern for RGB Xmas tree for the Raspberry Pi 4 available from Pi Hut.  
# See here for details:
# https://thepihut.com/products/3d-rgb-xmas-tree-for-raspberry-pi
#
# Installation:
# -------------
# See accompanying readme for full details on how to setup both the
# RGB Xmas tree as well as AWS Transcribe and Polly functionality on
# a Raspberry Pi 4.
#
# Implementation:
# --------------
# Uses PiHut's rgbxmastree tree module here: https://github.com/ThePiHut/rgbxmastree
# Download the tree module to your local directory as follows: wget https://bit.ly/2Lr9CT3 -O tree.py
# This script builds on PiHut's huecycle.py script cycling three groups of tree lights through 10 degrees 
# of hue and leaving the top light flashing white.
#
# History:
# -------
# 21.11.21    v0.1    First cut
# 29.11.21    v0.2    Updated this template
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


