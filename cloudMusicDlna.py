# -*- encoding:utf-8 -*-

# 网易云音乐dlna推送
# Sparkle
# v1.0


import gzip
import re
import os
import sys
import json
import getopt
import time
import socket
import select
import traceback
import signal
from contextlib import contextmanager

py3 = sys.version_info[0] == 3
if py3:
    from urllib import request
    from urllib import parse
    from urllib import error
else:
    from urllib2 import request
    from urllib2 import parse
    from urllib2 import error

# Ctrl + C 退出


def signal_handler(signal, frame):
    print('Ctrl + C, exit now...')
    sys.exit(1)


signal.signal(signal.SIGINT, signal_handler)

# 加载头部 防ban
opener = request.build_opener()
opener.addheaders = [
    ('Host', 'music.163.com'),
    ('Connection', 'keep-alive'),
    ('Cache-Control', 'max-age=0'),
    ('Upgrade-Insecure-Requests', '1'),
    ('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36'),
    ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3'),
    ('Accept-Encoding', 'gzip, deflate'),
    ('Accept-Language', 'zh-CN,zh-TW;q=0.9,zh;q=0.8,en-GB;q=0.7,en;q=0.6')
]
request.install_opener(opener)

# config
dlnaIp = ''
dlnaName = ''
playlistId = ''
musicId = ''

# config from dlna
SSDP_GROUP = ("239.255.255.250", 1900)
SSDP_ALL = "ssdp:all"
URN_AVTransport = "urn:schemas-upnp-org:service:AVTransport:1"
URN_AVTransport_Fmt = "urn:schemas-upnp-org:service:AVTransport:{}"
URN_RenderingControl = "urn:schemas-upnp-org:service:RenderingControl:1"
URN_RenderingControl_Fmt = "urn:schemas-upnp-org:service:RenderingControl:{}"

dlnaDevice = None

# ========================================================
# tool


def _get_tag_value(x, i=0):
    """ 用标签名获取里面的内容

    x -- xml string
    i -- position to start searching tag from
    return -- (tag, value) pair.
       e.g
          <d>
             <e>value4</e>
          </d>
       result is ('d', '<e>value4</e>')
    """
    x = x.strip()
    value = ''
    tag = ''

    # skip <? > tag
    if x[i:].startswith('<?'):
        i += 2
        while i < len(x) and x[i] != '<':
            i += 1

    # check for empty tag like '</tag>'
    if x[i:].startswith('</'):
        i += 2
        in_attr = False
        while i < len(x) and x[i] != '>':
            if x[i] == ' ':
                in_attr = True
            if not in_attr:
                tag += x[i]
            i += 1
        return (tag.strip(), '', x[i+1:])

    # not an xml, treat like a value
    if not x[i:].startswith('<'):
        return ('', x[i:], '')

    i += 1  # <

    # read first open tag
    in_attr = False
    while i < len(x) and x[i] != '>':
        # get rid of attributes
        if x[i] == ' ':
            in_attr = True
        if not in_attr:
            tag += x[i]
        i += 1

    i += 1  # >

    # replace self-closing <tag/> by <tag>None</tag>
    empty_elmt = '<' + tag + ' />'
    closed_elmt = '<' + tag + '>None</'+tag+'>'
    if x.startswith(empty_elmt):
        x = x.replace(empty_elmt, closed_elmt)

    while i < len(x):
        value += x[i]
        if x[i] == '>' and value.endswith('</' + tag + '>'):
            # Note: will not work with xml like <a> <a></a> </a>
            close_tag_len = len(tag) + 2  # />
            value = value[:-close_tag_len]
            break
        i += 1
    return (tag.strip(), value[:-1], x[i+1:])


def _xml2dict(s, ignoreUntilXML=False):
    """ xml转字典

    <?xml version="1.0"?>
    <a any_tag="tag value">
       <b> <bb>value1</bb> </b>
       <b> <bb>value2</bb> </b>
       </c>
       <d>
          <e>value4</e>
       </d>
       <g>value</g>
    </a>

    =>

    { 'a':
      {
          'b': [ {'bb':value1}, {'bb':value2} ],
          'c': [],
          'd':
          {
            'e': [value4]
          },
          'g': [value]
      }
    }
    """
    if ignoreUntilXML:
        s = ''.join(re.findall(".*?(<.*)", s, re.M))

    d = {}
    while s:
        tag, value, s = _get_tag_value(s)
        value = value.strip()
        isXml, dummy, dummy2 = _get_tag_value(value)
        if tag not in d:
            d[tag] = []
        if not isXml:
            if not value:
                continue
            d[tag].append(value.strip())
        else:
            if tag not in d:
                d[tag] = []
            d[tag].append(_xml2dict(value))
    return d


def _unescape_xml(xml):
    """ 还原xml中被转换的字符 < > "
    """
    return xml.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')


def _xpath(d, path):
    """ 使用标签路径获取xml内容
    标签名/标签名@标签中的参数=值

    d -- xml dictionary
    path -- string path like root/device/serviceList/service@serviceType=URN_AVTransport/controlURL
    return -- value at path or None if path not found
    """

    for p in path.split('/'):
        tag_attr = p.split('@')
        tag = tag_attr[0]
        if tag not in d:
            return None

        attr = tag_attr[1] if len(tag_attr) > 1 else ''
        if attr:
            a, aval = attr.split('=')
            for s in d[tag]:
                if s[a] == [aval]:
                    d = s
                    break
        else:
            d = d[tag][0]
    return d


def _url_get_json_load(url):
    """ 发送请求并解析json
    """

    gzdata = ''
    try:
        gzdata = request.urlopen(url)
    except:
        print('connect error: ', url)
        return {'code': ''}
    try:
        if isinstance(gzdata, str):
            return json.load(gzdata)
        else:
            gziper = gzip.GzipFile(fileobj=gzdata)
            return json.load(gziper)
    except Exception as e:
        print('decode error: ', url)
        return {'code': traceback.format_exc()}


@contextmanager
def _send_udp(to, packet):
    """ 群发udp

    to -- (host, port) group to send the packet to
    packet -- message to send
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.sendto(packet.encode(), to)
    yield sock
    sock.close()


def _send_tcp(to, payload):
    """ 群发tcp

    to -- (host, port) group to send to payload to
    payload -- message to send
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(to)
        sock.sendall(payload.encode('utf-8'))

        data = sock.recv(2048)
        if py3:
            data = data.decode('utf-8')
        data = _xml2dict(_unescape_xml(data), True)

        errorDescription = _xpath(
            data, 's:Envelope/s:Body/s:Fault/detail/UPnPError/errorDescription')
        if errorDescription is not None:
            print('send tcp error:', errorDescription)
    except Exception as e:
        data = ''
    finally:
        sock.close()
    return data

# ========================================================
# cloud music api


def getPlaylist(id=playlistId):
    """ 获取歌单
    由于接口的原因只会返回前1000首
    """

    url = 'http://music.163.com/api/playlist/detail?id=' + playlistId
    dataL = _url_get_json_load(url)
    if dataL['code'] != 200:
        ecode = str(dataL['code'])
        print('errorCode: ' + ecode)
    else:
        return dataL['result']


def playPlaylist(id, seek, track):
    """ 播放歌单id
    """
    pl = getPlaylist(id)
    if pl:
        print('playlist:', pl['name'])
        allNum = len(pl['tracks'])
        if not track or track < 1 or track > allNum:
            track = 1
        for index in range(track-1, allNum):
            print(pl['tracks'][index]['name'])
            playMusic(pl['tracks'][index]['id'], seek)
            # 等他放完
            time.sleep(pl['tracks'][index]['bMusic']['playTime'] / 1000)


def playMusic(id, seek='00:00:00'):
    """ 播放歌曲id
    """
    url = 'http://music.163.com/song/media/outer/url?id=' + str(id) + '.mp3'
    playUrl(url)


# ========================================================
# dlna api

def _get_port(location):
    """ Extract port number from url.

    location -- string like http://anyurl:port/whatever/path
    return -- port number
    """
    port = re.findall('http://.*?:(\d+).*', location)
    return int(port[0]) if port else 80


def _get_control_url(xml, urn):
    """ Extract AVTransport contol url from device description xml

    xml -- device description xml
    return -- control url or empty string if wasn't found
    """
    return _xpath(xml, 'root/device/serviceList/service@serviceType={}/controlURL'.format(urn))


def ontrol_url(xml, urn):
    """ Extract AVTransport contol url from device description xml

    xml -- device description xml
    return -- control url or empty string if wasn't found
    """
    return _xpath(xml, 'root/device/serviceList/service@serviceType={}/controlURL'.format(urn))


def _get_location_url(raw):
    """ Extract device description url from discovery response

    raw -- raw discovery response
    return -- location url string
    """
    t = re.findall('\n(?i)location:\s*(.*)\r\s*', raw, re.M)
    if len(t) > 0:
        return t[0]
    return ''


def _get_friendly_name(xml):
    """ Extract device name from description xml

    xml -- device description xml
    return -- device name
    """
    name = _xpath(xml, 'root/device/friendlyName')
    return name if name is not None else 'Unknown'


def _get_serve_ip(target_ip, target_port=80):
    """ Find ip address of network interface used to communicate with target

    target-ip -- ip address of target
    return -- ip address of interface connected to target
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((target_ip, target_port))
    my_ip = s.getsockname()[0]
    s.close()
    return my_ip


class DlnapDevice:
    """ 从dlnap复制来的 dlna 控制api
    """

    def __init__(self, raw, ip):

        self.ip = ip
        self.ssdp_version = 1

        self.port = None
        self.name = 'Unknown'
        self.control_url = None
        self.rendering_control_url = None
        self.has_av_transport = False

        try:
            self.__raw = raw.decode()
            self.location = _get_location_url(self.__raw)
            self.port = _get_port(self.location)
            raw_desc_xml = request.urlopen(self.location).read().decode()
            self.__desc_xml = _xml2dict(raw_desc_xml)
            self.name = _get_friendly_name(self.__desc_xml)
            self.control_url = _get_control_url(
                self.__desc_xml, URN_AVTransport)
            self.rendering_control_url = _get_control_url(
                self.__desc_xml, URN_RenderingControl)

            self.has_av_transport = self.control_url is not None
        except Exception as e:
            print('DlnapDevice (ip = {}) init exception:\n{}'.format(
                ip, traceback.format_exc()))

    def __repr__(self):
        return '{} @ {}'.format(self.name, self.ip)

    def __eq__(self, d):
        return self.name == d.name and self.ip == d.ip

    def _payload_from_template(self, action, data, urn):
        """ Assembly payload from template.
        """
        fields = ''
        for tag, value in data.items():
            fields += '<{tag}>{value}</{tag}>'.format(tag=tag, value=value)

        payload = """<?xml version="1.0" encoding="utf-8"?>
         <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
            <s:Body>
               <u:{action} xmlns:u="{urn}">
                  {fields}
               </u:{action}>
            </s:Body>
         </s:Envelope>""".format(action=action, urn=urn, fields=fields)
        return payload

    def _create_packet(self, action, data):
        """ Create packet to send to device control url.

        action -- control action
        data -- dictionary with XML fields value
        """
        if action in ["SetVolume", "SetMute", "GetVolume"]:
            url = self.rendering_control_url
            urn = URN_RenderingControl_Fmt.format(self.ssdp_version)
        else:
            url = self.control_url
            urn = URN_AVTransport_Fmt.format(self.ssdp_version)
        payload = self._payload_from_template(
            action=action, data=data, urn=urn)

        packet = "\r\n".join([
            'POST {} HTTP/1.1'.format(url),
            'User-Agent: Sparkle',
            'Accept: */*',
            'Content-Type: text/xml; charset="utf-8"',
            'HOST: {}:{}'.format(self.ip, self.port),
            'Content-Length: {}'.format(len(payload)),
            'SOAPACTION: "{}#{}"'.format(urn, action),
            'Connection: close',
            '',
            payload,
        ])

        return packet

    def set_current_media(self, url, instance_id=0):
        """ Set media to playback.

        url -- media url
        instance_id -- device instance id
        """
        packet = self._create_packet('SetAVTransportURI', {
                                     'InstanceID': instance_id, 'CurrentURI': url, 'CurrentURIMetaData': ''})
        _send_tcp((self.ip, self.port), packet)

    def play(self, instance_id=0):
        """ Play media that was already set as current.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'Play', {'InstanceID': instance_id, 'Speed': 1})
        _send_tcp((self.ip, self.port), packet)

    def pause(self, instance_id=0):
        """ Pause media that is currently playing back.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'Pause', {'InstanceID': instance_id, 'Speed': 1})
        _send_tcp((self.ip, self.port), packet)

    def stop(self, instance_id=0):
        """ Stop media that is currently playing back.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'Stop', {'InstanceID': instance_id, 'Speed': 1})
        _send_tcp((self.ip, self.port), packet)

    def seek(self, position, instance_id=0):
        """
        Seek position
        """
        packet = self._create_packet(
            'Seek', {'InstanceID': instance_id, 'Unit': 'REL_TIME', 'Target': position})
        _send_tcp((self.ip, self.port), packet)

    def volume(self, volume=10, instance_id=0):
        """ Stop media that is currently playing back.

        instance_id -- device instance id
        """
        packet = self._create_packet('SetVolume', {
                                     'InstanceID': instance_id, 'DesiredVolume': volume, 'Channel': 'Master'})

        _send_tcp((self.ip, self.port), packet)

    def get_volume(self, instance_id=0):
        """
        get volume
        """
        packet = self._create_packet(
            'GetVolume', {'InstanceID': instance_id, 'Channel': 'Master'})
        _send_tcp((self.ip, self.port), packet)

    def mute(self, instance_id=0):
        """ Stop media that is currently playing back.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'SetMute', {'InstanceID': instance_id, 'DesiredMute': '1', 'Channel': 'Master'})
        _send_tcp((self.ip, self.port), packet)

    def unmute(self, instance_id=0):
        """ Stop media that is currently playing back.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'SetMute', {'InstanceID': instance_id, 'DesiredMute': '0', 'Channel': 'Master'})
        _send_tcp((self.ip, self.port), packet)

    def info(self, instance_id=0):
        """ Transport info.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'GetTransportInfo', {'InstanceID': instance_id})
        return _send_tcp((self.ip, self.port), packet)

    def media_info(self, instance_id=0):
        """ Media info.

        instance_id -- device instance id
        """
        packet = self._create_packet(
            'GetMediaInfo', {'InstanceID': instance_id})
        return _send_tcp((self.ip, self.port), packet)

    def position_info(self, instance_id=0):
        """ Position info.
        instance_id -- device instance id
        """
        packet = self._create_packet(
            'GetPositionInfo', {'InstanceID': instance_id})
        return _send_tcp((self.ip, self.port), packet)

    def set_next(self, url):
        pass

    def next(self):
        pass


def discover(name='', ip='', timeout=1, st=SSDP_ALL, mx=3, ssdp_version=1):
    """ 扫描dlna设备

    name -- name or part of the name to filter devices
    timeout -- timeout to perform discover
    st -- st field of discovery packet
    mx -- mx field of discovery packet
    return -- list of DlnapDevice
    """
    st = st.format(ssdp_version)
    payload = "\r\n".join([
        'M-SEARCH * HTTP/1.1',
        'User-Agent: Sparkle',
        'HOST: {}:{}'.format(*SSDP_GROUP),
        'Accept: */*',
        'MAN: "ssdp:discover"',
        'ST: {}'.format(st),
        'MX: {}'.format(mx),
        '',
        ''])
    devices = []
    with _send_udp(SSDP_GROUP, payload) as sock:
        start = time.time()
        while True:
            if time.time() - start > timeout:
                # timed out
                break
            r, w, x = select.select([sock], [], [sock], 1)
            if sock in r:
                data, addr = sock.recvfrom(1024)
                if ip and addr[0] != ip:
                    continue

                d = DlnapDevice(data, addr[0])
                d.ssdp_version = ssdp_version
                if d not in devices:
                    if not name or name is None or name.lower() in d.name.lower():
                        if not ip:
                            devices.append(d)
                        elif d.has_av_transport:
                            # no need in further searching by ip
                            devices.append(d)
                            break

            elif sock in x:
                raise Exception('Getting response failed')
            else:
                # Nothing to read
                pass
    return devices


def playUrl(url):
    """ 播放url
    """
    global dlnaDevice
    try:
        if not dlnaDevice:
            allDevices = discover(name=dlnaName, ip=dlnaIp)
            if not allDevices:
                print('No devices found')
                sys.exit(1)
            dlnaDevice = allDevices[0]

        d.stop()
        d.set_current_media(url=url)
        d.play()
        time.sleep(1)
        print(dlnaDevice.media_info())
    except Exception as e:
        print('Device is unable to play media.')
        print('Play exception:\n{}'.format(traceback.format_exc()))
        sys.exit(1)


if __name__ == '__main__':
    def help():
        print('cloudMusicDlna.py [--play] [--pause] [--stop] [--info] [-i <device ip>] [-d <device name>] [-l <playlist id>] [-s <song id>] [--vol <volume 0-100>] [--seek 00:00:00] [--track 1] [--url http://...]')

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hi:d:l:s:", [
                                   'help', 'play', 'pause', 'stop', 'info', 'vol=', 'seek=', 'track=', 'url='])
    except getopt.GetoptError:
        help()
        sys.exit(1)

    device = ''
    url = ''
    vol = 0
    seek = '00:00:00'
    track = 1
    timeout = 1
    action = ''
    compatibleOnly = True
    ssdp_version = 1
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            help()
            sys.exit(0)
        if opt == '--play':
            action = 'play'
        elif opt == '--pause':
            action = 'pause'
        elif opt == '--stop':
            action = 'stop'
        elif opt == '--info':
            action = 'info'
        elif opt == '-i':
            dlnaIp = arg
        elif opt == '-d':
            dlnaName = arg
        elif opt == '-l':
            playlistId = arg
        elif opt == '-s':
            musicId = arg
        elif opt == '--vol':
            vol = int(arg)
        elif opt == '--seek':
            seek = arg
        elif opt == '--track':
            track = int(arg)
        elif opt == '--url':
            url = arg

    # 根据条件扫描dlna设备
    allDevices = discover(name=dlnaName, ip=dlnaIp,
                          timeout=timeout, st=SSDP_ALL, ssdp_version=ssdp_version)
    if not allDevices:
        print('No devices found')
        sys.exit(1)

    print('Devices:')
    for d in allDevices:
        print(' {} {}'.format('[o]' if d.has_av_transport else '[x]', d))
    dlnaDevice = allDevices[0]
    print('Use:', dlnaDevice)

    # 执行action
    if action == 'play':
        dlnaDevice.play()
    elif action == 'pause':
        dlnaDevice.pause()
    elif action == 'stop':
        dlnaDevice.stop()
    elif action == 'info':
        print(dlnaDevice.media_info())
    else:
        if vol:
            dlnaDevice.volume(vol)
        if url:
            playUrl(url)
        elif musicId:
            playMusic(musicId, seek)
        elif playlistId:
            playPlaylist(playPlaylist, seek, track)
