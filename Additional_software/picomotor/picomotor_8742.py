"""Python Driver for NewFocus controllers

    __author__ = "Ben Hammel"
    __credits__ = ["Ben Hammel"]
    __maintainer__ = "Ben Hammel"
    __email__ = "bdhammel@gmail.com"
    __status__ = "Development"

Requirements:
    python (version > 2.7)
    re
    pyusb: $ pip install pyusb

    libusb-compat (USB backend): $ brew install libusb-compat

References:
    [1] pyusb tutorial, https://github.com/walac/pyusb/blob/master/docs/tutorial.rst
        5/13/2015
    [2] user manual, http://assets.newport.com/webDocuments-EN/images/8742_User_Manual_revB.pdf
        5/13/2015

Further Information:

Notes:
    1) Only tested with 1 8742 Model Open Loop Picomotor Controller.
    2) Multiple controllers implemented testwise (Nicolai)
    3) If USB connection to piezomotors fails it is worth checking that the right driver (libusb) is loaded
        (not default on Win7 or 10), check e.g. using Zadig http://zadig.akeo.ie/

TODO:
    2) Block illegal commands, not just commands with an invalid format
    3) Develop GUI
    4) Add in connection check.
        Execute the following commands ever .5 s
            1>1 MD?\r
            1>1 TP?\r
            1>2 TP?\r
            1>3 TP?\r
            1>4 TP?\r
            1>ERRSTR?\r
"""
import re
import sys
import usb.core
import usb.util
import numpy as np

if sys.version_info[0] > 2:
    raw_input = input  # in case python 3 is used

# Corrected by Simone (added - case for enabling - movement)
NEWFOCUS_COMMAND_REGEX = re.compile("([0-9]{0,1})([a-zA-Z?]{2,})([0-9+-]*)")

MOTOR_TYPE = {
    "0": "No motor connected",
    "1": "Motor Unknown",
    "2": "'Tiny' Motor",
    "3": "'Standard' Motor"
}


class Controller(object):
    """Picomotor Controller

    Example:

        >>> controller = Controller(idProduct=0x4000, idVendor=0x104d)
        >>> controller.command('VE?')

        >>> controller.console()
    """

    def __init__(self, idProduct, idVendor, identifier=None, check_motors=False):
        """Initialize the Picomotor class with the spec's of the attached device

        Call self._connect to set up communication with usb device and endpoints

        Args:
            idProduct (hex): Product ID of picomotor controller
            idVendor (hex): Vendor ID of picomotor controller
        """
        self.idProduct = idProduct
        self.idVendor = idVendor
        self.identifier = identifier
        self.check_motors = check_motors

        # find all matching controller
        devices = tuple(usb.core.find(find_all=True, idProduct=0x4000, idVendor=0x104d))

        if len(devices) is 0:
            raise ValueError('No Matching Devices found')

        if identifier:
            print('Trying to connect to Controller ', self.identifier)
        else:
            print('Controller Identifier not specified: Connecting to any 8742 Piezo Controller')

        for dev in devices:
            try:
                self._connect(dev)
                if self.get_identity() == self.identifier:  # check if right controller is connected
                    break   #todo: release USB device if wrong -> seems not to work properly atm!
            except:
                pass
        #todo: Check if right device was connected: necessary b/c if driver is not properly installed wrong controller might be connected anyway

    def _connect(self, dev):
        """Connect class to USB device

        Find device from Vendor ID and Product ID
        Setup taken from [1]

        Raises:
            ValueError: if the device cannot be found by the Vendor ID and Product
                ID
            Assert False: if the input and outgoing endpoints can't be established
        """
        # access given device
        self.dev = dev

        if self.dev is None:
            raise ValueError('Device not found')

        # set the active configuration. With no arguments, the first
        # configuration will be the active one
        self.dev.set_configuration()

        # get an endpoint instance
        cfg = self.dev.get_active_configuration()
        intf = cfg[(0, 0)]

        self.ep_out = usb.util.find_descriptor(
            intf,
            # match the first OUT endpoint
            custom_match= \
                lambda e: \
                    usb.util.endpoint_direction(e.bEndpointAddress) == \
                    usb.util.ENDPOINT_OUT)

        self.ep_in = usb.util.find_descriptor(
            intf,
            # match the first IN endpoint
            custom_match= \
                lambda e: \
                    usb.util.endpoint_direction(e.bEndpointAddress) == \
                    usb.util.ENDPOINT_IN)

        assert (self.ep_out and self.ep_in) is not None

        # Confirm connection to user
        resp = self.command('VE?')
        print("Connected to Motor Controller Model {}. Firmware {} {} {}".format(*resp.split(' ')))
        print('Identifier of Controller: ', self.get_identity())

        # Added by Simone
        if self.check_motors:
            # Check the motors to determine type'
            resp = self.command('MC')
            # Save to memory the new settings
            resp = self.command('SM')

        # print motors status
        for m in range(1, 5):
            resp = self.command("{}QM?".format(m))
            print("Motor #{motor_number}: {status}".format(motor_number=m, status=MOTOR_TYPE[resp[-1]]))
        print('\n')

    def send_command(self, usb_command, get_reply=False):
        """Send command to USB device endpoint

        Args:
            usb_command (str): Correctly formated command for USB driver
            got_reply (bool): query the IN endpoint after sending command, to
                get controller's reply

        Returns:
            Character representation of returned hex values if a reply is
                requested
        """
        self.ep_out.write(usb_command)
        if get_reply:
            return self.ep_in.read(100)

    def parse_command(self, newfocus_command):
        """Convert a NewFocus style command into a USB command

        Args:
            newfocus_command (str): of the form xxAAnn
                > The general format of a command is a two character mnemonic (AA).
                Both upper and lower case are accepted. Depending on the command,
                it could also have optional or required preceding (xx) and/or
                following (nn) parameters.
                cite [2 - 6.1.2]
        """
        m = NEWFOCUS_COMMAND_REGEX.match(newfocus_command)

        # Check to see if a regex match was found in the user submitted command
        if m:

            # Extract matched components of the command
            driver_number, command, parameter = m.groups()

            usb_command = command

            # Construct USB safe command
            if driver_number:
                usb_command = '1>{driver_number} {command}'.format(
                    driver_number=driver_number,
                    command=usb_command
                )
            if parameter:
                usb_command = '{command} {parameter}'.format(
                    command=usb_command,
                    parameter=parameter
                )

            usb_command += '\r'

            return usb_command
        else:
            print("ERROR! Command {} was not a valid format".format(
                newfocus_command
            ))

    def parse_reply(self, reply):
        """Take controller's reply and make human readable

        Args:
            reply (list): list of bytes returns from controller in hex format

        Returns:
            reply (str): Cleaned string of controller reply
        """

        # convert hex to characters
        reply = ''.join([chr(x) for x in reply])
        return reply.rstrip()

    def command(self, newfocus_command):
        """Send NewFocus formated command

        Args:
            newfocus_command (str): Legal command listed in usermanual [2 - 6.2]

        Returns:
            reply (str): Human readable reply from controller
        """
        usb_command = self.parse_command(newfocus_command)

        # if there is a '?' in the command, the user expects a response from
        # the driver
        if '?' in newfocus_command:
            get_reply = True
        else:
            get_reply = False

        reply = self.send_command(usb_command, get_reply)

        # if a reply is expected, parse it
        if get_reply:
            return self.parse_reply(reply)

    def get_identity(self):
        """ Returns unique MAC identifier of current controller (Workaround since *IDN? seems not to work)
        :return: str
        """
        resp = self.command('MACADDR?')
        identifier = resp.split(' ')[-1]
        return identifier

    def start_console(self):
        """Continuously ask user for a command
        """
        print('''
        Picomotor Command Line
        ---------------------------

        Enter a valid NewFocus command, or 'quit' to exit the program.

        Common Commands:
            xMV[+-]: .....Indefinitely move motor 'x' in + or - direction
                 ST: .....Stop all motor movement
              xPRnn: .....Move motor 'x' 'nn' steps
        \n
        ''')

        while True:
            command = raw_input("Input > ")
            if command.lower() in ['q', 'quit', 'exit']:
                break
            else:
                rep = self.command(command)
                if rep:
                    print("Output: {}".format(rep))

                    # Commands Library implemented by Simone

    def move_indefinitely(self, motor, direction):
        """
            motor int 1-4
            direction str + or -
        """
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        if direction not in ('+', '-'):
            raise ValueError('ERROR: Direction not Valid, please choose + or -')
        self.command('%dMV%s' % (motor, direction))

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
        self.command('{}PR{}{}'.format(motor, direction, steps))

    def stop(self):
        '''
        For safety reasons stop stops all the motors

        '''
        self.command('ST')

    def get_acceleration(self):
        '''
        I check the acceleration only in the first motor since it's always set the same

        :return: acceleration in steps/s^2
        '''
        rep = self.command('1AC?')
        return float(rep.split('>')[1])

    def set_acceleration(self, value):
        '''
        The acceleration is set for all the motors

        :param value: sets the acceleration in steps/s^2 (default = 100000)
        '''
        for motor in np.arange(1, 5):
            self.command('%dAC%d' % (motor, value))

    def get_speed(self):
        '''
        I check the speed only in the first motor since it's always set the same

        :return: speed in step/s
        '''
        rep = self.command('1VA?')
        return float(rep.split('>')[1])

    def set_speed(self, value):
        '''
        The speed is set for all the motors

        :param value: speed in steps/s
        '''
        for motor in np.arange(1, 5):
            self.command('%dVA%d' % (motor, value))

    def set_home_position(self, motor):
        '''
        Defines the actual position of the motor as "home" position (sets actual position to 0)

        :param motor: motor 1 to 4
        '''
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        self.command('%dDH' % (motor))

    def get_home_position(self, motor):
        '''
        gives the value for actual "home" position

        :param motor: motor 1 to 4
        '''
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        self.command('%dDH?' % (motor))

    def get_actual_position(self, motor):
        '''
        :param motor: motor 1 to 4
        :return: the actual position of the motor in steps relative to the home position
        '''
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        return self.command('%dTP?' % motor)[2:]

    def move_to_position(self, motor, value):
        '''
        moves the motor to the position relative to the "home" position

        :param motor: motor 1 to 4
        :param value: steps relative to home position
        '''
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        self.command('%dPA%d' % (motor, value))

    def motion_done_status(self, motor):
        '''
        :param motor: motor 1 to 4
        :return: 0 if motion is in progress, 1 if motion is done
        '''
        if motor - 1 not in np.arange(0, 3):
            raise ValueError('ERROR: Motor Code not Valid, please choose a number between 1 and 4')
        md = self.command('%dMD?' % motor)
        #print('md =' + str(md))
        return int(md[2:])

    def close(self):
        self.stop()
        self.dev.reset()


def main():
    '''
        print('\n\n')
        print('#'*80)
        print('#\tPython controller for NewFocus Picomotor Controller')
        print('#'*80)
        print('\n')

        idProduct = '0x4000'
        idVendor = '0x104d'

        if not (idProduct or idVendor):
            print('Run the following command in a new terminal window:')
            print('\t$ system_profiler SPUSBDataType\n')
            print('Enter Product ID:')
            idProduct = raw_input('> ')
            print('Enter Vendor ID:')
            idVendor = raw_input('> ')
            print('\n')

        # convert hex value in string to hex value
        idProduct = int(idProduct, 16)
        idVendor = int(idVendor, 16)

        # Initialize controller and start console
        controller = Controller(idProduct=idProduct, idVendor=idVendor)
        controller.start_console()
        '''

    # Testing multiple Controller:
    idProduct = '0x4000'
    idVendor = '0x104d'
    idProduct = int(idProduct, 16)
    idVendor = int(idVendor, 16)

    input = raw_input('Test 1 or 2?')
    if int(input) == 1:
        # Test1: only one controller/ unknown identifier:
        controller = Controller(idProduct=idProduct, idVendor=idVendor)
    if int(input) == 2:
        # Test2: connecting several controllers (ids must be matching with your setup -> test1)
        id1 = '7730'
        id2 = '7409'
        # controller = Controller(idProduct=idProduct, idVendor=idVendor)
        controller1 = Controller(identifier=id1, idProduct=idProduct, idVendor=idVendor)
        controller2 = Controller(identifier=id2, idProduct=idProduct, idVendor=idVendor)

        print('Speed Controller 1: ', controller1.get_speed())
        print('Speed Controller 2: ', controller2.get_speed())


if __name__ == '__main__':
    main()
