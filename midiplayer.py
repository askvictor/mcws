import glob
import math
import threading
import time
import traceback

import mido

import drum_set
import instruments_map
import message_utils
import ref_strings
import fileutils


class MidiPlayer(threading.Thread):

    def __init__(self, ws):
        threading.Thread.__init__(self)
        self.ws = ws
        self.playing = False
        self.mid = None
        self.setName('Midi Player Thread')
        self.setDaemon(True)
        self.isPlaying = False
        self.listFile()
        self.isClosed = False
        self.searchResult = []
        self.lastQuery = ""
        self.selector = "@a"

    async def play_note(self, midimsg, inst, pan, chanvol):
        origin = midimsg.note - 66
        instrument = instruments_map.inst_map[inst]
        pitch = 2 ** ((origin + instrument[1]) / 12)
        volume = midimsg.velocity / 127 * chanvol
        await self.ws.send(
            message_utils.cmd(
                "execute " + self.selector + " ~ ~ ~ playsound " + instrument[0] + " @s ^" + str(
                    math.asin(pan * 2) * -2.5464790894703255) + " ^ ^ " + str(
                    volume) + " " + str(pitch)))

    async def play_perc(self, midimsg, pan, chanvol):
        instrument = drum_set.drum_set[midimsg.note]
        pitch = 2 ** (instrument[1] / 12)
        volume = midimsg.velocity / 127 * chanvol
        await self.ws.send(
            message_utils.cmd(
                "execute " + self.selector + " ~ ~ ~ playsound " + instrument[0] + " @s ^" + str(
                    math.asin(pan * 2) * -2.5464790894703255) + " ^ ^ " + str(
                    volume) + " " + str(pitch)))

    def run(self):
        while True:
            if self.playing:
                inst = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                pan = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                channel_volume = [1, 1, 1, 1, 1, 1,
                                  1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
                self.isPlaying = True
                try:
                    for msg in self.mid.play():
                        if not self.playing:
                            self.isPlaying = False
                            break
                        if msg.type == "note_on" and msg.velocity != 0:
                            if msg.channel != 9:
                                message_utils.runmain(
                                    self.play_note(msg, inst[msg.channel], pan[msg.channel],
                                                   channel_volume[msg.channel]))
                            else:
                                message_utils.runmain(self.play_perc(
                                    msg, pan[msg.channel], channel_volume[msg.channel]))
                        elif msg.type == "program_change":
                            inst[msg.channel] = msg.program
                        elif msg.type == "control_change":
                            if msg.control == 10:
                                pan[msg.channel] = msg.value / 127 - 0.5
                            elif msg.control == 7:
                                channel_volume[msg.channel] = msg.value / 127

                except Exception as e:
                    traceback.print_exc()
                    message_utils.runmain(self.ws.send(message_utils.info(e)))
                    self.mid = None
                    self.isPlaying = False
                    self.playing = False
            else:
                time.sleep(0.05)

    async def set_midi(self, mid):
        try:
            self.mid = mido.MidiFile(mid)
        except Exception as e:
            await self.ws.send(message_utils.error(e))

    def play(self):
        self.playing = True

    async def stop(self):
        await self.ws.send(message_utils.info(ref_strings.midiplayer.stopping))
        self.playing = False
        while self.isPlaying:
            time.sleep(0.05)
        await self.ws.send(message_utils.info(ref_strings.midiplayer.stopped))
        return

    async def help(self):
        for i in ref_strings.midiplayer.help:
            await self.ws.send(message_utils.info(i + " , " + ref_strings.midiplayer.help[i]))

    async def parseCmd(self, args):

        try:
            if args[0] == "--help" or args[0] == "-h" or args[0] == "-?" or args == []:
                await self.help()

            elif args[0] == "--info" or args[0] == "-i":
                await self.ws.send(message_utils.info(ref_strings.midiplayer.info))
                await self.ws.send(message_utils.info(ref_strings.midiplayer.midicount.format(len(self.midils))))

            elif args[0] == "--list" or args[0] == "-ls":
                page = 1
                if len(args) != 1:
                    page = int(args[1])
                entries = message_utils.getPage(self.midils, page)
                await message_utils.printEntries(self.ws, entries)

            elif args[0] == "--stop" or args[0] == "-s":
                await self.stop()

            elif args[0] == "--play" or args[0] == "-p":
                arg1 = int(args[1])
                if arg1 < len(self.midils):
                    await self.stop()
                    await self.ws.send(
                        message_utils.info(ref_strings.midiplayer.load_song.format(self.midils[arg1])))
                    await self.set_midi(self.midils[arg1])
                    self.play()
                else:
                    await self.ws.send(message_utils.error(ref_strings.file_not_exists))

            elif args[0] == "--search" or args[0] == "-se":
                if args[1:] == []:
                    await self.ws.send(message_utils.error(ref_strings.search_error))
                    return
                keyword = " ".join(args[1:]).lower()
                results = self.search(keyword)
                if len(results) == 0:
                    await self.ws.send(message_utils.error(ref_strings.empty_result))
                else:
                    for i in results:
                        await self.ws.send(
                            message_utils.info(ref_strings.list_format.format(i[0], i[1])))
            elif args[0] == "--reload" or args[0] == "-re":
                await self.reload()
            else:
                await self.ws.send(message_utils.error(ref_strings.midiplayer.unknown_command))
        except IndexError as e:
            await self.ws.send(str(e))
        except ValueError:
            await self.ws.send(message_utils.error(ref_strings.midiplayer.invaild_id))
        except FileNotFoundError:
            await self.ws.send(message_utils.error(ref_strings.file_not_exists))
            await self.listFile()

    def close(self):
        self.isClosed = True

    def search(self, keyword):
        self.lastQuery = keyword
        results = []
        keyword = keyword.lower().split(' ')
        for i in range(len(self.midils)):
            element = self.midils[i].lower()
            priority = 0
            for j in keyword:
                if j in element:
                    priority += 1
            if priority == len(keyword):
                results.append((i, self.midils[i]))
        return results

    async def reload(self):
        self.listFile()
        await self.ws.send(message_utils.info(ref_strings.midiplayer.reload))

    def listFile(self):
        self.midils = fileutils.listFile("midis/", ("mid", "midi"))
