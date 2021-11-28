#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# my-voice-tree.py
# ----------------
# (c) 2021 Mal Minhas, <mal@malm.co.uk>
#
# my-voice-tree.py
# ----------------
# Voice controlled RGB Xmas tree from Pi Hut using AWS Transcribe.
# Builds on my-tree.py by adding voice support using AWS.
# Uttering "Christmas tree red" will make the Christmas tree flash red.
# Uttering "Christmas tree green" will make it flash green.
# Uttering "Christmas tree disco" will make it phase different disco hues.
# Uttering "Christmas tree phase" will make it phase with synced hue.
#
# Installation:
# -------------
# See accompanying readme for full details on how to setup both the
# RGB Xmas tree as well as AWS Transcribe and Polly functionality on
# a Raspberry Pi 4.
#
# Implementation:
# --------------
# Cooperative multitasking using Python asyncio to interleave between
# micStream and RGBXmasTree LEDs.
#
# History:
# -------
# 27.11.21    v0.1    First cut
# 28.11.21    v0.2    Voice control tested and working on Raspberry Pi
#

import asyncio
import sounddevice
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
#from aiopolly import Polly
#from aiopolly.types import AudioFormat, LanguageCode, VoiceID
from tree import RGBXmasTree
from colorzero import Color, Hue
from time import sleep
from random import random
import re

# Create an instance of an RGBXmasTree    
TREE = RGBXmasTree(brightness=0.3)
# LED number for star at the top of the tree
STAR = 3
TREE_LED_SET = [list(range(25)[::3]), list(range(25)[1::3]), list(range(25)[2::3])]
LAST_STATE = 'disco'
STATE = 'disco'
TEXT = 'This is some text from GPT 3'
SUPPORTED_COLORS = ['red','green','blue','yellow','orange','purple','white','black','brown','disco','phase']

class TranscribeEventHandler(TranscriptResultStreamHandler):
    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        #print("TranscribeEventHandler: ENTER")
        global STATE, LAST_STATE
        # Handles text transcriptions
        xmasTree = re.compile(r'christmas tree (\w+)')
        results = transcript_event.transcript.results
        for result in results:
            for i,alt in enumerate(result.alternatives):
                text = alt.transcript.lower()
                print(f"{i}:'{alt.transcript}'")
                xres = re.match(xmasTree,text)
                if xres and xres[1] in SUPPORTED_COLORS:
                    new_state = xres[1]
                    if STATE == new_state:
                        print(f"We are already in {STATE}")
                    else:
                        print(f"STATE CHANGE: {new_state}!")
                        LAST_STATE = STATE
                        STATE = new_state
                        if STATE == 'disco':
                            initXmasTree()
                    break
                elif ' talk to me ' in text:
                    new_state = 'speak'
                    print(f"STATE CHANGE: {new_state}!")
                    break
        #print("TranscribeEventHandler: EXIT")

async def micStream():
    # Wraps raw input stream for mic forwarding blocks to asyncio.Queue
    loop = asyncio.get_event_loop()
    input_queue = asyncio.Queue()

    def callback(indata, frame_count, time_info, status):
        loop.call_soon_threadsafe(input_queue.put_nowait, (bytes(indata), status))

    # audio stream params should mate the audio formats for the source language being used per:
    # https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html
    stream = sounddevice.RawInputStream(
        channels = 1,
        samplerate = 16000,
        callback = callback,
        blocksize = 1024*2,
        dtype = "int16",
    )
    # Initiate the audio stream and async yield the audio chunks when they become available
    with stream:
        while True:
            indata, status = await input_queue.get()
            yield indata, status

async def writeChunks(stream):
    print("writeChunks: ENTER")
    # Connect raw audio chunks generator from mic and pass along to transcription stream
    async for chunk, status in micStream():
        await stream.input_stream.send_audio_event(audio_chunk=chunk)
    await stream.input_stream.end_stream()
    print("writeChunks: EXIT")

def initXmasTree():
    print("initXmasTree")
    global TREE, STATE
    assert(STATE == 'disco')
    # Initialise the LEDs to starting colours
    colors = [Color('red'),Color('green'),Color('blue')]
    for i, leds in enumerate(TREE_LED_SET):
        for led in leds:
            TREE[led].color = colors[i]
    # Colour top LED white
    TREE[STAR].color = Color('white')

async def lightUpXmasTree():
    print("lightUpXmasTree: ENTER")
    initXmasTree()
    try:
        global TREE, TREE_LED_SET, SUPPORTED_COLORS, STATE, LAST_STATE
        while True:
            #print(f'XmasTree: {STATE} ({LAST_STATE})')
            if (STATE in ['disco','phase']):
                # Hue phase in a slow cycle through all colors
                for leds in TREE_LED_SET:
                    for led in leds:
                        TREE[led].color += Hue(deg=10)
                TREE[STAR].color = Color('white')
            elif STATE in SUPPORTED_COLORS:
                # Solid color
                for leds in TREE_LED_SET:
                    for led in leds:
                        TREE[led].color = Color(STATE)
                TREE[STAR].color = Color('white')
            else:
                print(f'Unknown state {STATE}')
            await asyncio.sleep(0.01) # non-blocking
            LAST_STATE = STATE
    except:
        print("Exiting tree")
        TREE.close()
    print("lightUpXmasTree: EXIT")
    raise KeyboardInterrupt

async def waitForPolly():
    global TEXT, STATE, LAST_STATE
    while True:
        asyncio.sleep(0.1)
        if STATE == 'speak':
            print(f"Writing text {TEXT} to {output}")
            output = 'output.mp3'
            speech = await polly.synthesize_speech(TEXT, voice_id=VoiceID.Joanna)
            await speech.save_on_disc(filename=output)
            print(f"Written {output}")
            STATE = LAST_STATE
            LAST_STATE = 'speak'
            print(f"Switching back to {STATE}")

async def initializeVoiceTree():
    # setup client with chosen AWS region
    client = TranscribeStreamingClient(region = "us-west-2")
    # start transcription to generate our async mic stream
    stream = await client.start_stream_transcription(
        language_code = "en-US",
        media_sample_rate_hz = 16000,
        media_encoding = "pcm",
    )
    handler = TranscribeEventHandler(stream.output_stream)
    #await asyncio.gather(writeChunks(stream), handler.handle_events(), lightUpXmasTree(), waitForPolly())
    await asyncio.gather(writeChunks(stream), handler.handle_events(), lightUpXmasTree())

    
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(initializeVoiceTree())
        loop.close()
    except KeyboardInterrupt:
        print('Exiting main loop')

    
