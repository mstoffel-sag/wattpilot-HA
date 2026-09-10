"""In-tree Wattpilot client for the Home Assistant integration.

Originally vendored from joscha82/wattpilot and already carrying local changes
(bcrypt auth for Wattpilot Flex, which upstream does not have). Upstream has
had no release since 0.2 in May 2022, so this copy - not PyPI - is what the
integration loads, and it is maintained here.

Pruned to what the integration actually uses. It reads properties out of
allProps and maps them itself in sensor.yaml / select.yaml, so it needs only:

    Wattpilot(ip, password, serial, cloud)
    connect() disconnect() send_update()
    register_property_callback() unregister_property_callback()
    .name .serial .connected .allProps .allPropsInitialized .cak

Removed: 28 typed accessors (voltage1, amps2, mode, carConnected, ...) that
nothing read, the seven class-level enum lookup tables that fed them, and the
CLI shell. Those tables were not merely dead weight - they were incomplete and
raised out of the websocket callback, killing the connection for values the
integration never consumed. carValues covered 1-4, so car=5 ("Error") raised
KeyError: 5; errValues covered 0-5, so err=14 ("NoComm") raised KeyError: 14.

Added: a reconnect supervisor and a real disconnect(). The old connect() ran a
bare run_forever(), so a clean close ended the thread permanently and the
integration served stale values indefinitely.

All authentication and protocol code is byte-identical to the version this was
pruned from.
"""

import websocket
import json
import hashlib
import random
import threading
import hmac
import logging
import base64
import bcrypt

from time import sleep
from types import SimpleNamespace

_LOGGER = logging.getLogger(__name__)

CONST_HASH_PBKDF2 = 'pbkdf2'
CONST_HASH_BCRYPT = 'bcrypt'
CONST_WPFLEX_DEVICETYPE='wattpilot_flex'

# Seconds between reconnect attempts after the charger disappears.
RECONNECT_SECONDS = 30

# Pushed through the property callback when the socket drops, so the
# integration can re-evaluate entity availability. Not a charger property.
CONNECTION_SENTINEL = '__wattpilot_connection__'
__version__ = '0.2.2c-ha1'  # pruned in-tree client, see module docstring

class LoadMode():
    """Wrapper Class to represent the Load Mode of the Wattpilot"""
    DEFAULT=3
    ECO=4
    NEXTTRIP=5


class Wattpilot(object):

    # These four are resolved by NAME from the entity YAML, not from Python:
    #   select.yaml  "options: lmoValues"  and  "options: ustValues"
    #   sensor.yaml  "source: attribute, id: AccessState / carConnected"
    # select.py does getattr(charger, <options value>) to build the option
    # list, so removing them silently breaks those entities. Keep them.
    #
    # Lookups below use .get() with a readable fallback. The original indexed
    # these tables directly, and they are incomplete - carValues had no entry
    # for 5 ("Error"), which raised KeyError inside the websocket callback and
    # killed the connection. 0 and 5 are filled in here.
    carValues = {0: "Unknown", 1: "no car", 2: "charging", 3: "ready", 4: "complete", 5: "Error"}
    acsValues = {0: "Open", 1: "Wait"}
    lmoValues = {3: "Default", 4: "Eco", 5: "Next Trip"}
    ustValues = {0: "Normal", 1: "AutoUnlock", 2: "AlwaysLock"}

    _AccessState = None
    _carConnected = None









    _authhashtype = CONST_HASH_PBKDF2
    _hashedpassword = b''

    @property
    def allProps(self):
        """Returns a dictionary with all properties"""
        return self._allProps

    @property
    def allPropsInitialized(self):
        """Returns true, if all properties have been initialized"""
        return self._allPropsInitialized



    
    

    

    @property
    def serial(self):
        """Returns the serial number of Wattpilot Device (read only)"""
        return self._serial
    @serial.setter
    def serial(self,value):
        self._serial = value
        if (self._password is not None) & (self._serial is not None):
            self.__update_hashedpassword(self._password,self._serial)
           
    @property
    def name(self):
        """Returns the name of Wattpilot Device (read only)"""
        return self._name


    @property
    def hostname(self):
        """Returns the DNS Hostname of Wattpilot Device (read only)"""
        return self._hostname

    @property
    def friendlyName(self):
        """Returns the friendly name of Wattpilot Device (read only)"""
        return self._friendlyName

    @property
    def manufacturer(self):
        """Returns the Manufacturer of Wattpilot Device (read only)"""
        return self._manufacturer

    @property
    def devicetype(self):
        return self._devicetype

    @property
    def protocol(self):
        return self._protocol
    
    @property
    def secured(self):
        return self._secured

    @property
    def password(self):
        return self._password
    @password.setter
    def password(self,value):
        self._password = value
        if (self._password is not None) & (self._serial is not None):
            self.__update_hashedpassword(self._password,self._serial)


    @property
    def url(self):
        return self._url
    @url.setter
    def url(self,value):
        self._url = value

    @property
    def connected(self):
        return self._connected






















    @property
    def AccessState(self):
        """Read by sensor.yaml as: source: attribute, id: AccessState"""
        return self._AccessState

    @property
    def carConnected(self):
        """Read by sensor.yaml as: source: attribute, id: carConnected"""
        return self._carConnected

    @property
    def cak(self):
        """Returns the API Key for Cloud API Access (read only)"""
        return self._cak


    def __str__(self):
        """Returns a String representation of the core Wattpilot attributes"""
        if self.connected:
            ret = "Wattpilot: " + str(self.name) + "\n"
            ret = ret +  "Serial: " + str(self.serial) + "\n"
            ret = ret +  "Connected: " + str(self.connected) + "\n"
            ret = ret + "Car Connected: " + str(self.carConnected) + "\n"
            ret = ret + "Charge Status " + str(self.AllowCharging) + "\n"
            ret = ret + "Mode: " + str(self.mode) + "\n"
            ret = ret + "Power: " + str(self.amp) + "\n"
            ret = ret +  "Charge: " + "%.2f" % self.power + "kW" + " ---- " + str(self.voltage1) + "V/" + str(self.voltage2) + "V/" + str(self.voltage3) + "V" + " -- "
            ret = ret + "%.2f" % self.amps1 + "A/" + "%.2f" % self.amps2 + "A/" + "%.2f" % self.amps3 + "A" + " -- "
            ret = ret + "%.2f" % self.power1 + "kW/" + "%.2f" % self.power2 + "kW/" + "%.2f" % self.power3 + "kW" + "\n"
        else:
            ret = "Not connected"

        return ret
    def connect(self):
        """Start the websocket and keep it up.

        The original ran a bare run_forever() in a daemon thread. A clean close
        - which is what switching the charger off produces - made run_forever
        return, the thread exit, and nothing ever restart it: the integration
        then served its last known values indefinitely. Measured once at 24 h
        of frozen data after a power-off.

        run_forever(reconnect=...) (websocket-client >= 1.3.2) retries
        internally; the surrounding loop covers older versions where it simply
        returns on close.
        """
        self._stop = False

        def _supervise():
            while not self._stop:
                try:
                    try:
                        self._wsapp.run_forever(reconnect=RECONNECT_SECONDS)
                    except TypeError:
                        # websocket-client too old for the reconnect kwarg
                        self._wsapp.run_forever()
                except Exception as e:
                    _LOGGER.warning("Websocket loop raised %s (%s)", str(e), type(e).__name__)
                if self._stop:
                    break
                self._set_disconnected()
                _LOGGER.warning("Charger websocket closed, reconnecting in %s seconds", RECONNECT_SECONDS)
                sleep(RECONNECT_SECONDS)

        self._wst = threading.Thread(target=_supervise, name='wattpilot-ws-supervisor', daemon=True)
        self._wst.start()
        _LOGGER.info("Wattpilot connected")

    def disconnect(self):
        """Close the socket and stop reconnecting.

        The integration previously had to reach into _wsapp directly because
        this did not exist ("workaround until wattpilot python package > 0.2
        with built in disconnect is released").
        """
        self._stop = True
        try:
            self._wsapp.close()
        except Exception as e:
            _LOGGER.debug("Closing websocket failed: %s", str(e))
        self._set_disconnected()
        _LOGGER.info("Wattpilot disconnected")

    def _set_disconnected(self):
        """Mark the socket down and tell the listener.

        Entities in the integration are push-driven, so without this nothing
        writes state while the socket is dead and available() - which does
        check connected - is never re-evaluated. They would keep showing stale
        values as if current.
        """
        self._connected = False
        if self._property_callback is not None:
            try:
                self._property_callback(CONNECTION_SENTINEL, False)
            except Exception as e:
                _LOGGER.debug("Connection sentinel callback failed: %s", str(e))

    def register_message_callback(self,callback_fn):
        """signature of callback_fn: (wsapp,msg)"""
        self._message_callback = callback_fn

    def unregister_message_callback(self):
        self._message_callback = None

    def register_property_callback(self,callback_fn):
        """signature of callback_fn: (name,value)"""
        self._property_callback = callback_fn

    def unregister_property_callback(self):
        self._property_callback = None

    def set_power(self,power):
        self.send_update("amp",power)

    def set_mode(self,mode):
        self.send_update("lmo",mode)


    def send_update(self,name,value):
        message = {}
        message["type"]="setValue"
        self.__requestid = self.__requestid+1
        message["requestId"]=self.__requestid
        message["key"]=name
        message["value"]=value
        if (self._secured is not None):
            if  (self._secured > 0):
                self.__send(message,True)
            else:
                self.__send(message)
        else:
            self.__send(message)

    def __update_property(self, name, value):
        """Store a property and notify the listener.

        Everything the integration reads comes out of allProps, and it maps
        raw values itself (see sensor.yaml / select.yaml). The library used to
        also translate a dozen properties into typed attributes via class-level
        lookup tables. Nothing consumed those attributes, and the tables were
        incomplete - carValues covered 1-4 so car=5 ("Error") raised
        KeyError: 5, and errValues covered 0-5 so err=14 ("NoComm") raised
        KeyError: 14. Both escaped into the websocket on_message callback and
        killed the connection, for values that were never read. They are gone.

        cak is kept because the integration reads charger.cak for cloud setup.
        """
        self._allProps[name] = value
        if name == "cak":
            self._cak = value
        elif name == "acs":
            self._AccessState = self.acsValues.get(value, "unknown (%s)" % (value,))
        elif name == "car":
            self._carConnected = self.carValues.get(value, "unknown (%s)" % (value,))
        if self._property_callback is not None:
            self._property_callback(name, value)


    def __on_hello(self,message):
        _LOGGER.info("Connected to WattPilot Serial %s",message.serial)
        if hasattr(message,"hostname"):
            self._name=message.hostname
        self.serial = message.serial
        if hasattr(message,"hostname"):
            self._hostname=message.hostname
        if hasattr(message,"version"):
            self._version=message.version
        self._manufacturer=message.manufacturer
        self._devicetype=message.devicetype
        self._protocol=message.protocol
        if hasattr(message,"secured"):
            self._secured=message.secured

    def __on_auth(self,wsapp,message):
        ran = random.randrange(10**80)
        self._token3 = "%064x" % ran
        self._token3 = self._token3[:32]
        if hasattr(message,'hash'):
            self._authhashtype = message.hash
        elif self._devicetype == CONST_WPFLEX_DEVICETYPE:
            self._authhashtype = CONST_HASH_BCRYPT
        self.__update_hashedpassword()
        hash1 = hashlib.sha256((message.token1.encode()+self._hashedpassword)).hexdigest()
        hash = hashlib.sha256((self._token3 + message.token2+hash1).encode()).hexdigest()
        response = {}
        response["type"] = "auth"
        response["token3"] = self._token3
        response["hash"] = hash

        self.__send(response)

    def __update_hashedpassword(self,password=None,serial=None):
        if password is None:
            password = self._password
        if serial is None:
            serial = self._serial        
        if (password is None) or (serial is None):
            _LOGGER.info("__update_hashedpassword: password or serial empty - keep current _hashedpassword")
            return
        _LOGGER.debug("__update_hashedpassword: generating password hash of type: %s", self._authhashtype)
        if self._authhashtype == CONST_HASH_PBKDF2:
            self._hashedpassword = base64.b64encode(hashlib.pbkdf2_hmac('sha512',password.encode(),self.serial.encode(),100000,256))[:32]
        elif self._authhashtype == CONST_HASH_BCRYPT:
            hashedpw = self.__bcrypt_hash_password(password, serial)
            self._hashedpassword = hashedpw.encode()
        else:
           _LOGGER.error("__update_hashedpassword: unknown authhashtype: %s", self._authhashtype)

    def __bcryptjs_base64_encode(self,b: bytes, length: int) -> str:
        #manual implementation of javascript bcrypt.encodeBase64 function
        #encodeBase64 from https://github.com/dcodeIO/bcrypt.js/blob/28e510389374f5736c447395443d4a6687325048/index.js#L1133C17-L1133C29
        #base64_encode from https://github.com/dcodeIO/bcrypt.js/blob/28e510389374f5736c447395443d4a6687325048/index.js#L427
        #BASE64_CODE from: https://github.com/dcodeIO/bcrypt.js/blob/28e510389374f5736c447395443d4a6687325048/index.js#L402C1-L403C80
        BASE64_CODE = list("./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")        
        off = 0
        rs = []
        
        if length <= 0 or length > len(b):
            raise ValueError(f"Illegal len: {length}")
            
        while off < length:
            c1 = b[off] & 0xff
            off += 1
            rs.append(BASE64_CODE[(c1 >> 2) & 0x3f])
            c1 = (c1 & 0x03) << 4
            if off >= length:
                rs.append(BASE64_CODE[c1 & 0x3f])
                break

            c2 = b[off] & 0xff
            off += 1
            c1 |= (c2 >> 4) & 0x0f
            rs.append(BASE64_CODE[c1 & 0x3f])
            c1 = (c2 & 0x0f) << 2
            if off >= length:
                rs.append(BASE64_CODE[c1 & 0x3f])
                break

            c2 = b[off] & 0xff
            off += 1
            c1 |= (c2 >> 6) & 0x03
            rs.append(BASE64_CODE[c1 & 0x3f])
            rs.append(BASE64_CODE[c2 & 0x3f])

        return "".join(rs)

    def __bcryptjs_encodeBase64(self,s: str, length: int) -> str:
        #helper wrapper function for __bcryptjs_base64_encode
        #ensures python & java encoding is handled equally
        if s.isdigit(): #numeric only serial
            vals = [ord(ch) - ord('0') for ch in s]
            b = bytes([0] * (length - len(vals)) + vals)
        else: #not sure about serials in future - fallback
            _LOGGER.error("__bcryptjs_encodeBase64: check serial string - should be digits only: %s", s)
            raise ValueError(f"Check serial string - should be digits only: %s", s)
        return self.__bcryptjs_base64_encode(b,length)
     
    def __bcrypt_hash_password(self,password,serial,iterations=8) -> str:
        #hash bassword bcrypt / wattpilot flex compatible
        password_hash_sha256 = hashlib.sha256(password.encode('utf-8')).hexdigest()
        serial_b64 = self.__bcryptjs_encodeBase64(serial, 16)
        salt = []
        salt.append("$2a$")
        if (iterations < 10):
            salt.append("0")
        salt.append(str(iterations))
        salt.append("$")
        salt.append(serial_b64)
        salt=''.join(salt)
        bsalt = salt.encode("utf-8")
        bpassword = password_hash_sha256.encode('utf-8')
        pwhash = bcrypt.hashpw(bpassword, bsalt)
        salt_length = len(salt)
        pwhash_sub = pwhash[salt_length:].decode('ascii')
        return pwhash_sub

    def __send(self,message,secure=False):
        # If the  connection to wattpilot is over a unsecure channel (http) all send messages are wrapped in
        # a "securedMsg" Message which contains the original messageobject and a sha256 HMAC Hashed created
        # using the password
        if secure:
            messageid=message["requestId"]
            payload=json.dumps(message)
            h = hmac.new(bytearray(self._hashedpassword), bytearray(payload.encode()), hashlib.sha256 )
            message={}
            message["type"]="securedMsg"
            message["data"]=payload
            message["requestId"]=str(messageid)+"sm"
            message["hmac"]=h.hexdigest()

        _LOGGER.debug("Message send: %s",json.dumps(message)  )
        self._wsapp.send(json.dumps(message))

    def __on_AuthSuccess(self,message):
        self._connected = True
        _LOGGER.info("Authentication successful")

    def __on_FullStatus(self,message):
        props = message.status.__dict__
        for key in props:
            self.__update_property(key,props[key])
        if hasattr(message,'partial') and not self._allPropsInitialized:
            self._allPropsInitialized = not message.partial
        else:
            self.__allPropsInitializedFallback=True

    def __on_AuthError(self,message):
        if message.message=="Wrong password":
            self._wsapp.close()
            _LOGGER.error("Authentication failed: %s" , message.message)

    def __on_DeltaStatus(self,message):
        self._allPropsInitialized=True # Assume all properties have been initialized when first delta status is received
        props = message.status.__dict__
        for key in props:
            self.__update_property(key,props[key])

    def __on_clearInverters(self,message):
        pass

    def __on_updateInverter(self,message):
        pass

    def __on_response(self,message):
        if message.success:
            props = message.status.__dict__
            for key in props:
                self.__update_property(key,props[key])
        else:
            _LOGGER.error("Error Sending Request %s. Message: %s" ,message.requestId,message.message)

    def __on_error(self,wsapp,err):
        # Previously: close(), blocking sleep(30), run_forever() - from inside
        # the callback thread, racing whoever else was reconnecting. connect()
        # owns reconnection now.
        _LOGGER.warning("Charger websocket error: %s", err)
        self._set_disconnected()

    def __on_close(self,wsapp,code,msg):
        # Must notify from here, not from the supervising loop: when the
        # reconnect kwarg is supported run_forever never returns, so the loop
        # body does not run - but on_close still fires on every drop.
        _LOGGER.warning("Charger websocket closed (code=%s)", code)
        self._set_disconnected()

    def __on_message(self, wsapp, message):
        ## called whenever a message through websocket is received
        _LOGGER.debug("Message received: %s", message)
        msg=json.loads(message, object_hook=lambda d: SimpleNamespace(**d))
        if (msg.type == 'hello'):  # Hello Message -> Received upon connection before auth
            self.__on_hello(msg)
        if (msg.type == 'authRequired'): # Auth Required -> Received after hello 
            self.__on_auth(wsapp,msg)
        if (msg.type == 'response'): # Response Message -> Received after sending a update and contains result of update
            self.__on_response(msg)
        if (msg.type == 'authSuccess'): #Auth Success -> Received after sending  correct authentication message
            self.__on_AuthSuccess(msg)
        if (msg.type == 'authError'): # AUth errot -> Received after sending incorrect authentication message (e.g. wrong password)
            self.__on_AuthError(msg)
        if (msg.type == 'fullStatus'): #Full Status -> Received after successfull connection. Contains all Properties of Wattpilot
            self.__on_FullStatus(msg)
        if (msg.type == 'deltaStatus'): # Delta Status -> Whenever a property changes a Delta Status is send
            self.__on_DeltaStatus(msg)
        if (msg.type == 'clearInverters'): # Unknown
            self.__on_clearInverters(msg)
        if (msg.type == 'updateInverter'): # Contains information of connected Photovoltaik inverter / powermeter
            self.__on_updateInverter(msg)
        if self._message_callback != None:
            self._message_callback(self,wsapp,msg,message)


    def __init__(self, ip ,password,serial=None,cloud=False):
        RECONNECTINTERVALL = 30

        self.__requestid=0
        self._name = None
        self._hostname = None
        self._friendlyName = None
        self._manufacturer = None
        self._devicetype = None
        self._protocol = None
        self._secured = None
        self._serial = None
        self._password = None

        self.password = password

        if(cloud):
            self._url= "wss://app.wattpilot.io/app/" + serial + "?version=1.2.9"
        else:
            self._url = "ws://"+ip+"/ws"
        self.serial = None
        self._connected = False
        self._allProps={}
        self._allPropsInitialized=False
        self._cak=None
        self._message_callback=None
        self._property_callback=None

        self._wst=threading.Thread()

        websocket.setdefaulttimeout(10)
        self._wsapp = websocket.WebSocketApp(self.url, on_message=self.__on_message, on_error=self.__on_error, on_close=self.__on_close)
        _LOGGER.info ("Wattpilot %s initilized",self.serial)

