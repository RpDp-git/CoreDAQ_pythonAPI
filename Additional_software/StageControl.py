
# -*- coding: utf-8 -*-
"""
Stage control for the piezoelectric motors

If USB connection issues:
    Use zadig (https://zadig.akeo.ie/)

    if no device is shown, Options > List All Devices
    select libusb-win32 as driver to be installed


"""
from tkinter import *
from time import time_ns
from picomotor import np_picomotor_lib as pm
import numpy as np
import warnings
import sys
warnings.simplefilter('ignore', UserWarning)

def key(event):
    event_symbol = '%r' % event.keysym
    event_symbol = event_symbol.replace("'", "")
    # print event_symbol
    status = action(event_symbol)
    picomotor_action_label.confixg(text=status)

def action(event_key):
    controller.set_speed(int(speedEntry.get()))

    if event_key == 'Escape':
        quit_button_pressed()
    elif event_key == 'Insert':
        status = 'Moving Z Up'
        controller.move_indefinitely(1, '-')
    elif event_key == 'Delete':
        status = 'Moving Z Down'
        controller.move_indefinitely(1, '+')
    elif event_key == 'Right': #'Up':
        status = 'Moving Up'
        controller.move_indefinitely(2, '+')
    elif event_key == 'Left': #'Down':
        status = 'Moving Down'
        controller.move_indefinitely(2, '-')
    elif event_key == 'Down': #'Left':
        status = 'Moving Left'
        controller.move_indefinitely(3, '+')
    elif event_key == 'Up': #'Right':
        status = 'Moving Right'
        controller.move_indefinitely(3, '-')
    elif event_key == 'End':
        status = 'Stop'
        controller.stop()

    elif event_key == 'Prior':
        speed = controller.get_speed()

        if float(speed) * 2 <= 2000:
            speed = int(speed * 2)
            controller.set_speed(speed)
            status = 'Speed %d' % speed
        else:
            speed = int(2000)
            controller.set_speed(speed)
            status = 'Speed Max Lim'
        speedEntry.delete(0, END)
        speedEntry.insert(0,speed)

    elif event_key == 'Next':
        speed = controller.get_speed()

        if speed * 0.5 >= 10:
            speed = int(speed / 2)
            controller.set_speed(speed)
            status = 'Speed %d' % speed
        else:
            speed = int(10)
            controller.set_speed(speed)
            status = 'Speed Min Lim'
        speedEntry.delete(0, END)
        speedEntry.insert(0, speed)

    else:
        status = 'Not Valid'
    return status

def quit_button_pressed():
    print('Quitting the program...')
    root.quit()
    root.destroy()
    controller.close()
    sys.exit()



# Here main starts:
root = Tk()

def stay_on_top():
   root.lift()
   root.after(200, stay_on_top)

# Picomotor Action Label
prompt = '  Ready to move...  '
picomotor_action_label = Label(root, text=prompt, width=len(prompt), bg='gray')

# Quit Button
quit_button = Button(root, text='Quit', command=quit_button_pressed)
root.protocol('WM_DELETE_WINDOW', quit_button_pressed)

# speed control
xPadding = (30,30)
speedLabel = Label(text="Steps/s")
speedEntry = Entry()
speedEntry.insert(0, "10")



# GUI grid
picomotor_action_label.grid(row=0, column=0)
quit_button.grid(row=1, column=0)
speedLabel.grid(row=2, column=0, columnspan=2, sticky='w', padx=xPadding, pady=(10,0))
speedEntry.grid(row=3, column=0, columnspan=2, sticky='ew', padx=xPadding, pady=(0,10))


idProduct = '0x4000'
idVendor = '0x104d'

controller = pm.Controller(int(idProduct, 16), int(idVendor, 16))

root.bind_all('<Key>', key)
stay_on_top()
root.mainloop()

