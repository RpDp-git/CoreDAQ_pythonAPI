import time

import serial
import numpy as np

no_of_tries = 200


class Controller(object):
    def __init__(self, port, baud=19200, default_speed=50, default_acc=5000):
        self.ser = serial.Serial(port, baud, timeout=0, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE)

        for motor in range(3):
            self.__send('typ a1 {:d}=0'.format(motor))
            self.__send('mpv a1 {:d}=0'.format(motor))
            self.__send('vel a1 {:d}={:d}'.format(motor, default_speed))
            self.__send('acc a1 {:d}={:d}'.format(motor, default_acc))

        # Enable Driver
        self.__send('mon')

    def close(self):
        self.ser.close()

    def __get_answer(self):
        data = self.ser.read(9999)
        t0 = time.time()
        while (len(data) <= 0) and (time.time() - t0 < 0.25):
            data = self.ser.read(9999)

        if len(data) <= 0:
            print('Error: no answer from device recieved')

        return data

    def __send(self, command):
        # print command
        tries = 0
        while True and tries < no_of_tries:
            self.ser.write(command + '\r\n')
            data = self.__get_answer()
            # if command is unknown, try again, else leave. maybe an error while writing..
            if data.find('?') < 0 and data.find('UNKNOWN') < 0 and data.find('COMMAND') < 0:
                break
            tries += 1
            self.ser.reset_output_buffer()
            self.ser.reset_input_buffer()
            if tries > no_of_tries - 3:
                time.sleep(0.5)
                print("last tries")
        return data

    def move_indefinitely(self, motor, direction):
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        if direction not in ('+', '-'):
            raise ValueError('ERROR: Direction not Valid, please choose + or -')

        self.__send('chl a1={:d}'.format(motor - 1))
        if direction == '+':
            self.__send('for a1')
        else:
            self.__send('rev a1')
        self.__send('go')

    def move_relatively(self, motor, direction, steps):
        '''
        moves the motor a given number of steps

        :param motor: 1-4
        :param direction: '+' or '-'
        :param steps: integer number greater than 0
        :return:
        '''

        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        if direction not in ('+', '-'):
            raise ValueError('ERROR: Direction not Valid, please choose + or -')
        if steps <= 0:
            raise ValueError('ERROR: Number of steps must be greater than 0')
        self.__send('chl a1={:d}'.format(motor - 1))
        if direction == '+':
            self.__send('rel a1 {:d} g'.format(steps))
        else:
            self.__send('rel a1 {:d} g'.format(-steps))

    def stop(self):
        # For safety reasons stop stops all the motors
        self.__send('sto')

    def get_acceleration(self):
        raise NotImplementedError

    def set_acceleration(self, value):
        # The acceleration is set for all the motors
        for motor in np.arange(3):
            self.__send('acc a{:d} 0={:d}'.format(motor, value))

    def get_speed(self):
        raise NotImplementedError

    def set_speed(self, value):
        # The acceleration is set for all the motors
        for motor in np.arange(3):
            self.__send('vel a1 {:d}={:d}'.format(motor, value))


def main():
    controller = Controller(port="COM5")
    controller.move_indefinitely(1, '+')
    time.sleep(3)
    controller.stop()


if __name__ == "__main__":
    main()
