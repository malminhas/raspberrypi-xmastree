#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# my-voice-tree.py
# ----------------
# c) 2021 Mal Minhas, <mal@malm.co.uk>
#
# my-voice-tree.py
# ----------------
# Voice controlled RGB Xmas tree from Pi Hut using AWS Transcribe.
# Builds on my-tree.py by adding voice support using AWS.
# Uttering "Christmas tree red" will make the Christmas tree flash red.
# Uttering "Christmas tree green" will make it flash green.
# Uttering "Christmas tree disco" will make it flash disco colours.
#
# Installation:
# -------------
# See accompanying readme for full details on how to setup both the
# RGB Xmas tree as well as AWS.
#
# History:
# -------
# 27.11.21    v0.1    First cut
#

import asyncio
import sounddevice
from amazon_transcibe.client import TranscribeStreamingClient
from amazon_transcibe.handlers import TranscribeResultsStreamHandler
from amazon_transcibe.model import TranscriptEvent

from tree import RGBXmasTree
from colorzero import Color, Hue
from time import sleep
from random import random

STATE = 'disco'

class TranscribeEventHandler(TranscriptResultStreamHandler):
    async def handle_transcript_event(self, transcritp_event: TranscriptEvent):
        print("TranscribeEventHandler: ENTER")
        # Handles text transcriptions
        results = transcript_event.transcript.results
        for result in results:
            for i,alt in enumerate(result.alternatives):
                print(f"{i}:{alt.transcript}")
                if alt.transcript.lower() == "christmas tree red":
                    print("got a red!")
                    global STATE='red'
                    break
        print("TranscribeEventHandler: EXIT")

async def micStream():
    # Wraps raw input stream for mic forwarding blocks to asyncio.Queue
    loop = asyncio.get_event_loop()
    input_queue = asyncio.Queue()

    def callback(indate, frame_count, time_info, status):
        loop.call_soon_threadsafe(input_queue.put_nowait, (bytes(indata), status))

    # audio stream params should mate the audio formats for the source language being used per:
    # https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html
    stream = sounddevice.RawInputStream(
        channels = 1,
        samplerate = 16000,
        callback = callback,
        blocksize = 1024*2
        dtype = "int16",
    )
    # Initiate the audio stream and async yield the audio chunks when they become available
    with stream:
        while True:
            indate, stats = await input_queue.get()
            yield indata, status

async def writeChunks(stream):
    print("writeChunks: ENTER")
    # Connect raw audio chunks generator from mic and pass along to transcription stream
    async for chunk, status in micStream():
        await stream.input_stream.send_audio_event(audio_chunk=chunk)
    await stream.input_stream.end_stream()
    print("writeChunks: EXIT")

def setupTranscribe():
    # setup client with chosen AWS region
    client = TranscribeStreamingClient(region = "us-west-2")
    # start transcripito to generate our async mic stream
    stream = await client.start_stream_transcription(
        language_code = "en-US",
        media_sample_rate_hz = 16000,
        media_encoding = "pcm",
    )
    return stream

async def lightUpXmasTree():
    # LED number for star at the top of the tree
    STAR = 3
    TREE_LED_SET = [list(range(25)[::3]), list(range(25)[1::3]), list(range(25)[2::3])]
    # Create an instance of an RGBXmasTree    
    tree = RGBXmasTree(brightness=0.1)
    # Initialise the LEDs to starting colours
    colors = [Color('red'),Color('green'),Color('blue')]
    for i, leds in enumerate(TREE_LED_SET):
        for led in leds:
            tree[led].color = colors[i]
    # Colour top LED white
    tree[STAR].color = Color('white')
    try:
        while True:
            if (STATE == 'disco'):
                # Hue phase in a slow cycle through all colors
                print('disco')
                for leds in LED_SET:
                    for led in leds:
                        tree[led].color += Hue(deg=10)
                tree[STAR].color = Color('white')
            elif STATE in ['red','green','blue','yellow']:
                # Solid color
                print(STATE)
                for leds in LED_SET:
                    for led in leds:
                        tree[led].color = Color(STATE)
                tree[STAR].color = Color('white')
            await asyncio.sleep(0.01) # non-blocking
    except:
        print("Exiting tree")
        tree.close()

async def initializeVoiceTree()
    stream = setupTranscribe()
    handler = TranscribeEventHandler(stream.output_stream)
    await asyncio.gather(writeChunks(stream), handler.handle_events(), lightUpXmasTree() )

    
if __name__ = '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(initialiseVoiceTree)
    loop.close()
    
 


