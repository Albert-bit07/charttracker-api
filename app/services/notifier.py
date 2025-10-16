import pygame,sys,time
from plyer import notification

pygame.mixer.init()

def notify(title:str,message:str,timeout:int=10):
    try:
        notification.notify(
            title=title,
            message=message,
            timeout=timeout
            )
    except Exception as e:
        print(f"[Error] Sending notification:{e}")

def playsound(result:str):
    """Play Win or lose sound"""
    file='Win.mp3' if result=="win" else 'Lose.mp3'
    try:
        pygame.mixer.music.load(file)
        pygame.mixer.music.play()
    except Exception as e:
        print(f"[Error] Playing sound:{e}")

        
    
                
