#!/usr/bin/python3
import os
import copy
import itertools
from pprint import pprint
import json
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GObject
from gi.repository import GLib

from camilladsp import CamillaConnection

DEBUG = False
HOST, PORT = "127.0.0.1", 31234
MIN_VOL = -99.5
VOL_STEP = 0.5
VOL_FORMAT = '{:5.1f}'

CONFIG_DIR = os.path.expanduser('~/my-camilladsp-config')

MENU_MAP = {'config': ('2.1 DRC', '2.1', '2.0', 'Mono'), 
            'source': ('Stream', 'Phono')}            
            
PLAYBACK_DEVICE = 'hw:CARD=M4,DEV=0'
PLAYBACK_CHANNELS = 4
CROSSOVER_FREQUENCY = 80
MAINS_DELAY = 9.2
SAMPLERATE = 44100
DRC_FILTER = os.path.join(CONFIG_DIR, 'filters/drc.wav')

# available key bindings for 'FLIRC XBMS' device on my Harmony 350 remote
KEYBINDINGS = {
   Gdk.KEY_BackSpace: 'nav_exit', # Exit and "go back arrow" buttons
   Gdk.KEY_Page_Up: 'config_next', # Up/Down rocker button next to Info
   Gdk.KEY_Page_Down: 'config_prev',
   Gdk.KEY_i: None, # Info
   Gdk.KEY_F2: None, # green
   Gdk.KEY_F3: None, # yellow
   Gdk.KEY_F4: None, # blue
   Gdk.KEY_AudioRaiseVolume: 'vol_up',
   Gdk.KEY_AudioLowerVolume: 'vol_down',
   Gdk.KEY_AudioMute: 'mute',
   Gdk.KEY_Up: 'nav_up', # Up/Down/Left/Right buttons in the arrow keys around OK
   Gdk.KEY_Left: 'nav_left', 
   Gdk.KEY_Right: 'nav_right',
   Gdk.KEY_Down: 'nav_down',
   Gdk.KEY_KP_Enter: 'nav_select', # OK button
   Gdk.KEY_AudioPlay: 'track_play', # Pause doesn't work for some reason
   Gdk.KEY_AudioRewind: None,
   Gdk.KEY_AudioForward: None,
   Gdk.KEY_AudioPrev: 'track_prev',
   Gdk.KEY_AudioNext: 'track_next',
   Gdk.KEY_AudioStop: 'track_stop',
   Gdk.KEY_1: None,
   Gdk.KEY_2: None,
   Gdk.KEY_3: None,
   Gdk.KEY_4: None,
   Gdk.KEY_5: None,
   Gdk.KEY_6: None,
   Gdk.KEY_7: None,
   Gdk.KEY_8: None,
   Gdk.KEY_9: None,
   Gdk.KEY_0: None,
   # added with the Harmony app
   Gdk.KEY_c: 'menu', # Menu key (for ContextMenu, I assume)
   Gdk.KEY_period: 'source_next', # Ch+ button
   Gdk.KEY_comma: 'source_prev', # Ch- button
   Gdk.KEY_Print: None, # Record button
}


CSS = b"""
* {
    background-color: black;
    color: orange;
}

label { 
    font: 35mm Sans;
    /* background-color: black; */
    margin-left: 2cm;
    /* margin-right: 2cm; */
}

label#dB {
    margin-left: 4mm;
    font: 20mm Sans;
    margin-right: 2cm;
}
"""


def get_channel_map(destinations, input_channels, mono=False, gain=0.0):
    if mono:
        mapping = [{'dest': i,
                    'mute': False,
                    'sources': [{'channel': j,
                                 'gain': gain,
                                 'inverted': False,
                                 'mute': False} for j in input_channels]
                    }
                   for i in destinations
                   ]
    else:
        mapping = [{'dest': i,
                    'mute': False,
                    'sources': [{'channel': j,
                                 'gain': gain,
                                 'inverted': False,
                                 'mute': False}]
                    }
                   for i, j in zip(destinations, input_channels)
                   ]
    return mapping


def create_config(routing='2.1',
                  input_source='Stream',
                  correction='DRC',
                  playback_device='hw:Headphone,0',
                  playback_channels=4,
                  samplerate=44100,
                  crossover=80,
                  delay=0.0,
                  drc_filter=None):
    input_channels = [0, 1]
    destinations = [0, 1]
    if input_source != 'Stream':
        input_channels = [i + 2 for i in input_channels]
        capture_device = playback_device
        capture_channels = playback_channels
    else:
        capture_device = 'hw:Loopback,1'
        capture_channels = 2
    config = {}
    config['devices'] = {
                            'adjust_period': 10.0,
                            'capture': {'avoid_blocking_read': False,
                                        'channels': capture_channels,
                                        'device': capture_device,
                                        'format': 'S32LE',
                                        'retry_on_error': False,
                                        'type': 'Alsa'},
                            'capture_samplerate': 0,
                            'chunksize': 8192,
                            'enable_rate_adjust': True,
                            'enable_resampling': False,
                            'playback': {'channels': playback_channels,
                                         'device': playback_device,
                                         'format': 'S32LE',
                                         'type': 'Alsa'},
                            'queuelimit': 4,
                            'resampler_type': 'BalancedAsync',
                            'samplerate': samplerate,
                            'silence_threshold': 0.0,
                            'silence_timeout': 0.0,
                            'target_level': 0
                        }

    if routing == 'Mono':  # each destination gets both input channels mixed
        mapping = get_channel_map(destinations, input_channels, mono=True,
                                  gain=-6.0)
    else:  # stereo, each destination gets one input channel respectively
        mapping = get_channel_map(destinations, input_channels)
    mixer_name = f'{input_source}-{routing}'
    config['mixers'] = {mixer_name:
                            {'channels': {'in': capture_channels, 
                                          'out': playback_channels},
                             'mapping': mapping}}
    # We always have a volume filter present
    filters = config['filters'] = {'volume': {'type': 'Volume',
                                              'parameters':
                                                  {'ramp_time': 200.0}
                                              }
                                   }

    # Start building the pipeline
    pipeline = config['pipeline'] = []
    input_filters = [
        {'type': 'Filter', 'channel': i, 'names': ['volume']}
        for i in input_channels]
    if correction == 'DRC':
        for i, filt in enumerate(input_filters):
            name = f'drc_{i}'
            filters[name] = {'type': 'Conv',
                             'parameters': {'type': 'Wav',
                                            'filename': drc_filter,
                                            'channel': i}}
            input_filters[i]['names'].append(name)
    pipeline.extend(input_filters)
    pipeline.append({'type': 'Mixer', 'name': mixer_name})
    if routing in ['2.1', '2.2']:
        sub_destinations = [i + 2 for i in destinations]
        if routing in '2.1':  # send mono mix to sub channel
            sub_destinations = sub_destinations[:1]
            sub_map = get_channel_map(sub_destinations, input_channels,
                                      mono=True)
        elif routing == '2.2':  # stereo subs
            sub_map = get_channel_map(sub_destinations, input_channels)
        mapping.extend(sub_map)
        crossover_filters = {
            'sublowpass': {'type': 'BiquadCombo',
                           'parameters': {
                               'type': 'LinkwitzRileyLowpass',
                               'freq': crossover,
                               'order': 8}},
            'mainshighpass': {'type': 'BiquadCombo',
                              'parameters': {
                                  'type': 'LinkwitzRileyHighpass',
                                  'freq': crossover,
                                  'order': 8}},
            'mainsdelay': {'type': 'Delay',
                           'parameters': {
                               'delay': delay,
                               'unit': 'ms',
                               'subsample': False}}
        }
        filters.update(crossover_filters)

        mains_output_filters = [{'type': 'Filter',
                                 'channel': i,
                                 'names': ['mainshighpass', 'mainsdelay']}
                                for i in destinations]
        sub_output_filters = [{'type': 'Filter',
                               'channel': i,
                               'names': ['sublowpass']}
                              for i in sub_destinations]
        pipeline.extend(mains_output_filters)
        pipeline.extend(sub_output_filters)
    return config


def get_screen_size():
    display = Gdk.Display.get_default()
    n_monitors = display.get_n_monitors()
    monitor_map = {}
    for m in range(n_monitors):
        monitor = display.get_monitor(m)
        geometry = monitor.get_geometry()
        return geometry.width, geometry.height

    
class MyWindow(Gtk.Window):
    def __init__(self):
        super().__init__()
        print('DEBUG:', DEBUG)
        # CSS boilerplate
        screen = Gdk.Screen.get_default()
        provider = Gtk.CssProvider()
        style_context = Gtk.StyleContext()
        style_context.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        provider.load_from_data(CSS)
        self.cdsp = CamillaConnection(HOST, PORT)
        self.cdsp.connect()
        # create and validate all user selectable configurations
        self.config_map = {}
        self.create_configs()
        self.config_label = Gtk.Label(name='config')
        self.source_label = Gtk.Label(name='source')
        self.volume_label = Gtk.Label(name='volume')
        #self.db_label = Gtk.Label(label='\N{Square Db}', name='dB')
        self.db_label = Gtk.Label(label='dB', name='dB')
        self.volume_label.set_xalign(1.0)
        self.config_label.set_xalign(0.0)
        self.source_label.set_xalign(0.0)
        self.db_label.set_xalign(0.0)
        self.db_label.set_yalign(0.7)
        #self.volume_label.set_width_chars(10)
        #self.volume_label.set_max_width_chars(10)
        vbox = Gtk.VBox()
        vbox.pack_start(self.source_label, True, True, 0)
        hbox = Gtk.HBox()
        hbox.pack_start(self.volume_label, True, True, 0) 
        hbox.pack_start(self.db_label, False, True, 0)
        vbox.pack_start(hbox, True, True, 0) 
        vbox.pack_start(self.config_label, True, True, 0) 
        #self.volume_label.set_padding(100, 100)
        self.set_volume()
        self.load_config_object(MENU_MAP['config'][0], MENU_MAP['source'][0])
        #self.set_config_name()
        #self.config_label.set_text('2.1 DRC')
        #self.source_label.set_text('Stream')
        self.add(vbox)
        # The fullscreen() method does not seem to work.
        # Needs a window manager?  So we find screen dims
        # and set window size "manually".
        width, height = get_screen_size()
        self.resize(width, height)
        self.connect("key-press-event", self.on_key_press_event)
        self.start_mute_timer()

    def create_configs(self):
        """Create user-selectable Camilla config objects and validate them."""
        for config in MENU_MAP['config']:
            for input_source in MENU_MAP['source']:
                routing, *correction = config.split()
                correction = correction[0] if correction else ''
                camilla_config_obj = create_config(routing=routing,
                                                   input_source=input_source,
                                                   correction=correction,
                                                   playback_device=PLAYBACK_DEVICE,
                                                   playback_channels=PLAYBACK_CHANNELS,
                                                   crossover=CROSSOVER_FREQUENCY,
                                                   delay=MAINS_DELAY,
                                                   samplerate=SAMPLERATE,
                                                   drc_filter=DRC_FILTER
                                    )
                validated = self.cdsp.validate_config(camilla_config_obj)
                self.config_map[(config, input_source)] = validated

    def on_key_press_event(self, widget, event):
        print('keyval:', Gdk.keyval_name(event.keyval))
        action = KEYBINDINGS.get(event.keyval)
        if action:
            method = getattr(self, 'on_'+action)
            method()

    def on_mute(self):
        #print('Toggling mute...')
        muted = self.cdsp.get_mute()
        self.cdsp.set_mute(not muted)
        if self.cdsp.get_mute():
            self.start_mute_timer()

    def on_vol_down(self):
        #print('Volume down...')
        vol = self.cdsp.get_volume()
        if vol > MIN_VOL:
            self.cdsp.set_volume(vol - VOL_STEP)
        self.set_volume()

    def on_vol_up(self):
        #print('Volume up...')
        vol = self.cdsp.get_volume()
        if vol <= -VOL_STEP:
            self.cdsp.set_volume(vol + VOL_STEP)
        self.set_volume()
        
    def on_source_next(self):
        print('on_source_next')
        self.menu_step('source', +1)
        
    def on_source_prev(self):
        print('on_source_prev')
        self.menu_step('source', -1)

    def on_config_next(self):
        print('on_config_next')
        self.menu_step('config', +1)
        
    def on_config_prev(self):
        print('on_config_prev')
        self.menu_step('config', -1)
        
    def on_track_play(self):
        print('on_track_play')
        
    def on_track_next(self):
        print('on_track_next')

    def on_track_prev(self):
        print('on_track_prev')

    def on_track_stop(self):
        print('on_track_stop')
        
    def on_menu(self):
        print('on_menu')
        
    def on_nav_left(self):
        self.set_balance(side='left')
        
    def on_nav_right(self):
        self.set_balance(side='right')

    def on_nav_up(self):
        print('on_nav_up')

    def on_nav_down(self):
        print('on_nav_down')
        
    def on_nav_select(self):
        print('on_nav_select')
        
    def on_nav_exit(self):
        print('on_nav_exit')
        
    def start_mute_timer(self):
        if self.cdsp.get_mute():
            self.start_mute_timer()
                
    def get_balance(self):
        config_obj = self.cdsp.get_config()
        gain0 = config_obj['filters']['balance0']['parameters']['gain']
        gain1 = config_obj['filters']['balance1']['parameters']['gain']
        return config_obj, gain0, gain1
        
    def set_balance(self, side='left'):
        config_obj, gain0, gain1 = self.get_balance()
        if side == 'left':
            if gain0 == 0.0:
                new_gain0 = 0.0
                new_gain1 = gain1 - VOL_STEP
            else:
                new_gain0 = gain0 + VOL_STEP
                new_gain1 = 0.0
        elif side == 'right':
            if gain1 == 0.0:
                new_gain1 = 0.0
                new_gain0 = gain0 - VOL_STEP
            else:
                new_gain1 = gain1 + VOL_STEP
                new_gain0 = 0.0
        if side in ['left', 'right']:
            print('set_balance:', new_gain0, new_gain1)
            config_obj['filters']['balance0']['parameters']['gain'] = new_gain0
            config_obj['filters']['balance1']['parameters']['gain'] = new_gain1
            self.cdsp.set_config(config_obj)

    def set_config_name(self, config='', source=''):
        #path = self.cdsp.get_config_name()
        #basename = os.path.basename(path)
        #routing, source = CONFIG_DESC[basename]
        self.config_label.set_text(config)
        self.source_label.set_text(source)
        
    def load_config_by_desc(self, config='', source=''):
        basename = f"{source}-{config.replace(' ', '-')}.yml"
        path = os.path.join(CONFIG_DIR, basename)
        self.cdsp.set_config_name(path)
        self.cdsp.reload()
        #self.set_config_name(config=config, source=source)
        self.config_label.set_text(config)
        self.source_label.set_text(source)
        
    def load_config_object(self, config='', source=''):
        config_obj = self.config_map[(config, source)]
        self.cdsp.set_config(config_obj)
        self.config_label.set_text(config)
        self.source_label.set_text(source)
        
    def menu_step(self, key, step=1):
        current_map = {key: getattr(self, f'{key}_label').get_text()
                       for key in MENU_MAP}
        menu_items = MENU_MAP[key]
        current_index = menu_items.index(current_map[key])
        next_index = (current_index + step) % len(menu_items)
        next = menu_items[next_index]
        current_map[key] = next
        #self.load_config_by_desc(**current_map)
        self.load_config_object(**current_map)
        
    def set_volume(self):
        vol = self.cdsp.get_volume()
        label = VOL_FORMAT.format(vol)
        self.volume_label.set_text(label)

    def blink_vol(self):
        if not self.cdsp.get_mute():
            self.volume_label.set_opacity(self.volume_label_opacity)
            return False
        opacity = 0 if self.volume_label.get_opacity() else self.volume_label_opacity
        self.volume_label.set_opacity(opacity)
        return True

    def start_mute_timer(self):
        self.volume_label_opacity = self.volume_label.get_opacity()
        GLib.timeout_add(500, self.blink_vol)

        
win = MyWindow()
win.connect("destroy", Gtk.main_quit)
win.show_all()
Gtk.main()
