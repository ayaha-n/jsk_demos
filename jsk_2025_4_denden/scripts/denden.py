#!/usr/bin/env python
import os
import shutil
import array
import asyncio
import numpy as np
import rospy
import soundfile as sf
from std_msgs.msg import String
from ros_speak import play_sound
from pathlib import Path
import json
from emotion_analyzer.srv import AnalyzeAudio

def classify_emotions(response):
#classify emotions into 7 categories
    star_scores = []
    heart_scores = []
    sad_scores = []
    angry_scores = []
    tired_scores = []
    happy_scores = []
    neutral_scores = []
    result = json.loads(response)
    if result["prosody"]:
        emotion_data = result["prosody"]
    elif result["burst"]:                                                                         
        emotion_data = result["burst"]   
    else:
        rospy.logwarn("No valid emotion data found.")
        return
    
    for emotion in emotion_data:
        if emotion['name'] in ['Excitement','Interest','Admiration','Surprise (positive)','desire','Triumph', 'Ecstasy', 'Joy','Satisfaction','Amusement']: 
            star_scores.append(emotion['score'])
        elif emotion['name'] in ['Adoration','Love','Entrancement','Romance', 'Relief','Aesthetic Appreciation','Contentment']:
            heart_scores.append(emotion['score'])
        elif emotion['name'] in ['Awkwardness','Disappointment','Distress','Anxiety','Sadness','Pain','Surprise (negative)','Fear','Empathic Pain','Horror', 'Guilt']:
            sad_scores.append(emotion['score'])  
        elif emotion['name'] in ['Anger', 'Disgust', 'Contempt']:                                               angry_scores.append(emotion['score'])  
        elif emotion['name'] in ['Boredom','Tiredness']:     
            tired_scores.append(emotion['score'])
        elif emotion['name'] in ['Calmness','Awe','Confusion','Embarrassment','Envy','Sympathy','Pride','Realization','Determination','Nostalgia','Craving','Concentration','Contemplation','Doubt','Shame']:                
            neutral_scores.append(emotion['score'])
            
    avg_star_score = sum(star_scores)/len(star_scores) if star_scores else 0
    avg_heart_score = sum(heart_scores)/len(heart_scores) if heart_scores else 0  
    avg_sad_score = sum(sad_scores)/len(sad_scores) if sad_scores else 0  
    avg_angry_score = sum(angry_scores)/len(angry_scores) if angry_scores else 0  
    avg_tired_score = sum(tired_scores)/len(tired_scores) if tired_scores else 0  
    avg_neutral_score = sum(neutral_scores)/len(neutral_scores) if neutral_scores else 0

    state = max([
        ('star', avg_star_score),
        ('heart', avg_heart_score),
        ('sad', avg_sad_score),
        ('angry', avg_angry_score),
        ('tired', avg_tired_score),
        ('neutral', avg_neutral_score)
    ], key=lambda x: x[1])
    global state_name
    state_name, state_score = state
    
    print(f"The dominant emotion is {state_name} with a score of {state_score}")
    
def set_eye_status(state_name):
    eye_status_message = String(data=state_name)
    eye_status_publisher.publish(eye_status_message) 

# Service Proxy to call the 'analyze_audio' service
def call_analyze_audio_service(audio_file=''):
    rospy.wait_for_service('/analyze_audio')  # Wait until the service is available
    try:
        analyze_audio_service = rospy.ServiceProxy('/analyze_audio', AnalyzeAudio)
        response = analyze_audio_service(audio_file)  # Calling the service with the audio_file argument
        classify_emotions(response.result)  # Pass the result to classify_emotions function
    except rospy.ServiceException as e:
        rospy.logerr(f"Service call failed: {e}")

    
rospy.init_node('denden')

play_sound('package://jsk_2025_4_denden/resource/purugacha.wav', topic_name='robotsound_\
jp', wait=True) 

eye_status_publisher = rospy.Publisher('/eye_status', String, queue_size=1)

state_name = 'neutral'  #set initial state

call_analyze_audio_service(audio_file='') 

