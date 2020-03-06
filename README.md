# 网易云歌单dlna推送
写这个东西源自于吃饭时的一个脑洞

dlna是怎么工作的，为什么app关掉了播放器还能继续播放直到这首音乐放完？

进过抓包测试，是通过http post方式传输xml来告诉播放器播放哪个url的。

然后就写了这个东西，可以把它跑在电脑，手机，kindle，服务器，树莓派上，甚至跑在esp8266上（（（

这个程序会按着歌单的顺序一首首的把歌推给dlna播放设备（比如音乐盒子，智能音响，电视等）

其实Android端的网易云就有dlna这个功能XD（虽然经常放着放着就炸了）

额外的功能：可以直接指定给播放的url，可以是电台，视频等等设备能直接访问的内容

### 如何使用
需要python3

```cloudMusicDlna.py [--play] [--pause] [--stop] [--info] [-i <device ip>] [-d <device name>] [-l <playlist id>] [-s <song id>] [-v <volume 0-100>] [--seek 00:00:00] [-t <trackNum>] [-u http://...] [--urlNext http://...] [-k]```

参数 | 描述
---- | -----
play,pause,stop | 为播放控制，因为把程序关掉，播放也会到当前歌曲放完了才会停止
info | 当前播放媒体信息
i | 指定设备ip
d | 指定设备名称
l | 歌单id
s | 歌曲id
vol | 音量 范围0-100
seek | 开始时间轴
track | 歌单中开始的曲目
url | 指定url播放
urlNext | 指定下一个播放的url
k | 在kindle上使用mplayer播放音乐的姿势，会在屏幕左上角输出播放曲目数

Tips：实测在kindle上进行dlna推送需要在iptables允许所有入站请求

### 更新日志
2020/02/04 在最后一分钟写完了第一版

2020/02/05 修复在python3.5下的json库的奇怪bug

2020/02/05 增加urlNext连播功能

2020/02/07 增加播放进度，更准确的播放显示，修复bug

2020/03/07 增加在kindle上播放音乐的功能，你说没有耳机孔？用USB DAC转接线呀OTG就可以
