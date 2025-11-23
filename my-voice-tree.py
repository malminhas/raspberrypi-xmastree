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
# 28.11.21    v0.3    Added support for wait looping on network
# 28.11.21    v0.4    Added basic support for playing back mp3 and using Polly
#

import re
import os
import sys
import vlc
import time
import boto3
import awscrt
import asyncio
import threading
import sounddevice
from boto3 import Session
from botocore.exceptions import BotoCoreError, ClientError
from contextlib import closing
from asyncio.subprocess import PIPE
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
from tree import RGBXmasTree
from colorzero import Color, Hue
from time import sleep
from random import random

# Create an instance of an RGBXmasTree
TREE = RGBXmasTree(brightness=0.3)
# LED number for star at the top of the tree
STAR = 3
TREE_LED_SET = [list(range(25)[::3]), list(range(25)[1::3]), list(range(25)[2::3])]
LAST_STATE = 'disco'
PLAYING = False
STATE = 'disco'
TEXT = 'Hello everyone this is your Christmas Tree talking'
AUDIO = ''
SUPPORTED_COLORS = ['red','green','blue','yellow','orange','purple',
                    'white','pink','black','brown','disco','phase']

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

class TranscribeEventHandler(TranscriptResultStreamHandler):
    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        #print("TranscribeEventHandler: ENTER")
        # Handle text transcriptions
        xmasTree = re.compile(r'(christmas tree)(\.|\,|s)?\s+(\w+)(.*)')
        results = transcript_event.transcript.results
        for result in results:
            for i,alt in enumerate(result.alternatives):
                global STATE, LAST_STATE, TEXT, AUDIO
                text = alt.transcript.lower()
                print(f"{i}:'{alt.transcript}' ({text})")
                xres = re.match(xmasTree,text)
                def switchState(new_state):
                    global STATE, LAST_STATE
                    if STATE == new_state:
                        print(f"We are already in STATE {STATE} - skipping")
                    else:
                        LAST_STATE = STATE
                        STATE = new_state
                        print(f"STATE CHANGE: '{new_state}' LAST_STATE={LAST_STATE}")
                if xres:
                    print(f"MATCH! xres[0]='{xres[0]}',xres[1]='{xres[1]}',\
                            xres[2]='{xres[2]}',xres[3]='{xres[3]}',xres[4]='{xres[4]}'")
                    command = xres[3].lower()
                    if command in SUPPORTED_COLORS:
                        switchState(command)
                        if STATE in ['disco']:
                            initXmasTree(darkMode=False)
                        break
                    elif command in ['speak','talk','talked']:
                        AUDIO = 'speech.mp3'
                        switchState('speak')
                        break
                    elif command in ['sing','saying','black mirror']:
                        AUDIO = '08-I-Wish-it-Could-be-Christmas-Everyday.mp3'
                        switchState('speak')
                        break
                    elif command == 'generate':
                        TEXT = xres[4].replace('.','')
                        if len(TEXT.strip()) >= 10:
                            #TEXT = "You didn't give me anything to generate"
                            switchState('generate')
                            break
                    else:
                        print(f"Cannot handle '{command}'")
        #print("TranscribeEventHandler: EXIT")

def initXmasTree(darkMode):
    print(f"initXmasTree(darkMode={darkMode})")
    global TREE, STATE
    if darkMode:
        STATE = 'black'
        for i, leds in enumerate(TREE_LED_SET):
            for led in leds:
                TREE[led].color = Color('black')
        # Colour top LED white
        TREE[STAR].color = Color('black')
    else:
        assert(STATE in ['disco'])
        # Initialise the LEDs to starting colours
        colors = [Color('red'),Color('green'),Color('blue')]
        for i, leds in enumerate(TREE_LED_SET):
            for led in leds:
                TREE[led].color = colors[i]
        # Colour top LED white
        TREE[STAR].color = Color('white')

async def lightUpXmasTree():
    print("lightUpXmasTree: ENTER")
    initXmasTree(darkMode=False)
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
                LAST_STATE = STATE
            elif STATE in SUPPORTED_COLORS:
                # Solid color
                for leds in TREE_LED_SET:
                    for led in leds:
                        TREE[led].color = Color(STATE)
                if STATE not in ['black']:
                    TREE[STAR].color = Color('white')
                LAST_STATE = STATE
            else:
                # print(f"Skipping unknown state {STATE}')
                pass
            await asyncio.sleep(0.01) # non-blocking
    except:
        print("Exiting tree")
        TREE.close()
    print("lightUpXmasTree: EXIT")
    raise KeyboardInterrupt

def playMp3(file,length):
    global PLAYING
    if PLAYING:
        print(f"Already playing song")
        return
    print(f"playMp3({file})")
    PLAYING = True
    player = vlc.MediaPlayer(file)
    player.play()
    time.sleep(length)
    player.stop()
    PLAYING = False

def generateMp3WithPolly(text, file):
    """ From AWS Getting Started Example """
    print(f"Generating polly file {file} from: '{text}'")
    polly_client = boto3.Session(region_name='us-west-2').client('polly')
    response = polly_client.synthesize_speech(VoiceId='Joanna',
                OutputFormat='mp3', 
                Text = text,
                Engine = 'neural')
    file = open(file, 'wb')
    file.write(response['AudioStream'].read())
    file.close()
    
async def waitForPolly():
    print("waitForPolly: ENTER")
    global TEXT, STATE, LAST_STATE, AUDIO
    while True:
        await asyncio.sleep(0.1)
        #print(f"polly state: {STATE}")
        if STATE == 'speak':
            # Initially tried this using asyncio.create_subprocess_exec using a local script
            """
            speechFile = 'speech2.mp3'
            process = await asyncio.create_subprocess_exec(
                'python',
                'testVlc.py',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process = await asyncio.create_subprocess_shell(
                f'python testVlc.py',
                stdout=PIPE,
                stderr=PIPE,
            )
            print("going into process communicate")
            (output,err) = await process.communicate()
            status = await process.wait()
            print(f"dropping out of await. STATE={STATE}, LAST_STATE={LAST_STATE}")
            """
            cwd = os.environ.get("WORKING_DIR")
            if not cwd:
                cwd = '.'
            speechFile = f'{cwd}/{AUDIO}'
            length = 360
            if AUDIO == 'speech.mp3':
                length = 10
            print(f"Using vlc to play {speechFile} - non-blocking")
            # Switched to using threads to avoid blocking
            x2 = threading.Thread(target=playMp3, args=(speechFile,length), daemon=False)
            x2.start()
            #x2.join() # uncomment this to block on completion
            print(f"dropping out after starting vlc thread. STATE={STATE}, LAST_STATE={LAST_STATE}")
            STATE = LAST_STATE
            LAST_STATE = 'speak'
            print(f"Switching back to {STATE}")
        elif STATE == 'generate':
            cwd = os.environ.get("WORKING_DIR")
            if not cwd:
                cwd = '.'
            speechFile = f'{cwd}/generate.mp3'
            print(f"Generating speech file {speechFile} - blocking")
            x1 = threading.Thread(target=generateMp3WithPolly, args=(TEXT,speechFile,), daemon=False)
            x1.start()
            x1.join() # uncomment this to block on completion
            print(f"Using vlc to play {speechFile} - non-blocking")
            # Switched to using threads to avoid blocking
            x2 = threading.Thread(target=playMp3, args=(speechFile,), daemon=False)
            x2.start()
            #x2.join() # uncomment this to block on completion
            print(f"dropping out after starting vlc thread. STATE={STATE}, LAST_STATE={LAST_STATE}")
            STATE = LAST_STATE
            LAST_STATE = 'speak'
            print(f"Switching back to {STATE}")


def synthesizeText(text):
    polly_client = boto3.Session(region_name='us-west-2').client('polly')
    response = polly_client.synthesize_speech(VoiceId='Joanna',
                                              OutputFormat='mp3', 
                                              Text = 'This is a sample text to be synthesized.',
                                              Engine = 'neural')
    with open('speech.mp3', 'wb'):
        file.write(response['AudioStream'].read())

            
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
    await asyncio.gather(writeChunks(stream), handler.handle_events(), lightUpXmasTree(), waitForPolly())
    #await asyncio.gather(writeChunks(stream), handler.handle_events(), lightUpXmasTree())

if __name__ == '__main__':
    def initialiseLoop():
        ret = 0
        print("initialise loop")
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            loop.run_until_complete(initializeVoiceTree())
            print('closing loop')
            loop.close()
        except KeyboardInterrupt:
            print('Exiting main loop')
        except awscrt.exceptions.AwsCrtError as e:
            # Can get here on boot with:
            # AWS_IO_DNS_QUERY_FAILED: A query to dns failed to resolve.
            print('Caught awscrt.exceptions.AwsCrtError')
            ret = -1
        except Exception as e:
            print(e)
            ret = -1
        return ret

    while True:
        r = initialiseLoop()
        if r == 0:
            sys.exit(0)
        else:
            retry = 2
            print(f'Retrying after {retry} secs..')
            sleep(retry)
