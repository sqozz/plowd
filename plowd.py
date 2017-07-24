#!/usr/bin/env python3
import subprocess, threading, queue, os
import socket, socketserver
import hashlib
from functools import partial
import json
import pdb
import time
import logging
import sys
from urllib.parse import urlparse
import configparser
from jsonrpc import JSONRPCResponseManager, Dispatcher

VERSION = "0.0.1+42"

# TODO: Returncode von plowshare auswerten
# TODO: Checksummen prüfen (md5 für SO)
# TODO: Downloads neu anwerfen bei fehlern
# TODO: Entpacken von downloads
# TODO: IPC Schnittstelle
# TODO: Persistieren und laden


# TO BE IMPLEMENTED:
# - status
# - modify downloader (e.g. threads to download)
# - start downloads
# - stop downloads
# - create container
# - modify container

class API():
	def __init__(self, downloaderInstance):
		self.pdInstance = downloaderInstance
		self.__responseManager__ = JSONRPCResponseManager()

		self.__dispatcher__ = Dispatcher()
		self.__dispatcher__["getVersion"] = self.__getVersion__
		self.__dispatcher__["getStatus"] = self.__getStatus__
		self.__dispatcher__["setMaxDownloaderThreads"] = self.__setMaxDownloaderThreads__

	def handle(self, data):
		return self.__responseManager__.handle(data, self.__dispatcher__)

	def __getVersion__(self):
		return VERSION

	def __getStatus__(self):
		return self.pdInstance.getStatus()

	def __setMaxDownloaderThreads__(self, maxThreads = 0):
		self.pdInstance.setThreadMax(maxThreads)
		return True


class IPC():
	__instance = None

	def __init__(self, host, port, downloaderInstance):
		self.__server = None
		if not IPC.__instance:
			IPC.__instance = IPC.__IPC(host, port, downloaderInstance)
		else:
			IPC.__instance.host = host
			IPC.__instance.port = port
			IPC.__instance.pdInstance = downloaderInstance

	def __getattr__(self, name):
		return getattr(self.__instance, name)

	class __IPC:
		def __init__(self, host, port, downloaderInstance):
			self.host = host
			self.port = port
			self.pdInstance = downloaderInstance
			self.__api = API(self.pdInstance)
			self.__receiveQueue = queue.Queue()
			self.__sendQueue = queue.Queue()

		def __str__(self):
			return "Inner class of IPC (named __IPC)"

		def startIPC(self):
			self.__server = IPC.ThreadedTCPServer.withAPI((self.host, self.port), IPC.ThreadedTCPRequestHandler, self.__api)
			self.port = self.__server.server_address[1] # IP of this new server. Mostly relevant to update port 0 to the random port
			self.__serverThread = threading.Thread(target=self.__server.serve_forever)
			self.__serverThread.daemon = True
			self.__serverThread.start()
	
		def stopIPC(self):
			if self.__server is not None:
				self.__server.shutdown()
				self.__server.server_close()

	class ThreadedTCPRequestHandler(socketserver.BaseRequestHandler):
		def handle(self):
			data = self.request.recv(1024)
			returnData = self.server.getApi().handle(data)
			response = returnData.json
			self.request.sendall(response.encode("UTF-8"))

	class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
		@classmethod
		def withAPI(__class, port_host, handlerClass, api):
			__class.__api = api
			return __class(port_host, handlerClass)

		def getApi(self):
			return self.__api


class QueueDownloader(threading.Thread):
	__isRunning = True
	__queue = queue.Queue()
	__runningThreads = list()
	isQueueEmpty = True
	idle = True
	__username = ""
	__password = ""
	__maxThreads = 5
	__status = {
		"runningThreads": "",
		"maxThreads": "",
		"queueSize": "",
		"idle": ""
	}

	def __init__(self, maxThreads = 5):
		self.__log = logging.getLogger("QueueDownloader")
		self.__maxThreads = maxThreads
		threading.Thread.__init__(self)
		self.__status_sema = threading.BoundedSemaphore()

	def stop(self):
		self.__isRunning = False

	def getStatus(self):
		with self.__status_sema:
			return self.__status

	def setUsername(self, username):
		self.__username = username

	def setPassword(self, password):
		self.__password = password

	def setThreadMax(self, newMax):
		self.__maxThreads = newMax

	def run(self):
		while self.__isRunning:
			time.sleep(.1)
			self.keepRunningFilled()
			self.__collectStats()

	def __collectStats(self):
		with self.__status_sema:
			self.__status["runningThreads"] = len(self.__runningThreads)
			self.__status["maxThreads"] = self.__maxThreads
			self.__status["queueSize"] = self.__queue.qsize()
			self.__status["idle"] = self.idle

	def addLink(self, linkObj):
		self.isQueueEmpty = False
		self.__queue.put(linkObj)

	def getRunning(self):
		return self.__runningThreads

	def cleanupThreads(self):
		for thread in self.__runningThreads:
			if not thread.isAlive():
				self.__runningThreads.remove(thread)

	def createThread(self, linkObj):
		dlThread = DownloadThread(linkObj, location = linkObj.getLocation() )
		dlThread.setUsername(self.__username)
		dlThread.setPassword(self.__password)
		linkObj.assignDownloadThread(dlThread)
		self.__runningThreads.append(dlThread)
		self.idle = False
		dlThread.start()

	def keepRunningFilled(self):
		if len(self.__runningThreads) < self.__maxThreads and not self.__queue.empty():
			self.isQueueEmpty = False
			linkObj = self.__queue.get()
			self.createThread(linkObj)
		else:
			self.cleanupThreads()
			if self.__queue.empty():
				self.isQueueEmpty = True
				if len(self.__runningThreads) == 0:
					if not self.idle:
						self.idle = True
						self.__log.info("Nothing left do download")
			else:
				self.isQueueEmpty = False

class PlowDown:
	__downloadList = list() #List of link collections
	__downloader = None
	__dlThreadMax = 5

	def __init__(self, dlThreadMax = 5, username = "", password = ""):
		self.__log = logging.getLogger("PlowDown")
		self.__username = username
		self.__password = password
		self.__dlThreadMax = dlThreadMax
		self.__IPC = IPC("127.0.0.1", 0, self)
		self.__IPC.startIPC()
		self.__log.info("Listening on {} port {}".format(self.__IPC.host, self.__IPC.port))

	def startCNLListener(self):
		import clicknload
		clicknload.start_webserver()

	def stopDownloader(self):
		self.__downloader.stop()

	def stopIPC(self):
		self.__IPC.stopIPC()

	def setThreadMax(self, maxThreads):
		self.__dlThreadMax = maxThreads
		if self.__downloader is not None:
			self.__downloader.setThreadMax(self.__dlThreadMax)

	def addLinkCollection(self, collection):
		self.__downloadList.append(collection)
		if self.__downloader is not None:
			for link in collection.getLinklist():
				self.__downloader.addLink(link)

	def getCollections(self):
		return self.__downloadList

	def startDownloader(self):
		if self.__downloader is None:
			self.__downloader = QueueDownloader(maxThreads = self.__dlThreadMax)
			for collection in self.getCollections():
				for link in collection.getLinklist():
					self.__downloader.addLink(link)
			self.__downloader.setUsername(self.__username)
			self.__downloader.setPassword(self.__password)
			self.__downloader.start()
		else:
			print("Downloader already started")

	def getStatus(self):
		return self.__downloader.getStatus()

	def idle(self):
		return self.__downloader.idle

class LinkCollection():
	downloadLocation = ""
	name = ""

	def __init__(self, location, name = "", links = [] ):
		self.name = name
		self.__links = []
		self.downloadLocation = location
		if type(links) == type([]) and len(links) > 0:
			for link in links:
				self.addLink(link)
		elif type(links) == type("of_string"):
			self.addLink(links)

	def getTotalSize(self):
		totalSize = 0
		for link in self.__links:
			totalSize += int(link.filesize)
		return totalSize

	def getTotalProgress(self):
		totalProgress = 0
		if len(self.__links) > 0:
			for link in self.__links:
				totalProgress += int(link.getStatus()["percent"])
			totalProgress /= len(self.__links)
		return totalProgress

	def getTotalLoaded(self):
		totalLoaded = 0
		for link in self.__links:
			totalLoaded += str(link.getStatus()["received"])

	def getLinklist(self):
		return self.__links

	def getName(self):
		return self.name

	def setName(self, name):
		self.name = name

	def getLocation(self):
		return self.downloadLocation

	def setLocation(self, location):
		self.downloadLocation = location

	def addLink(self, link):
		if type(link) == type("of_string") and len(link) > 0 and link not in (link.url for link in self.__links):
			self.__links.append(Link(link, self.downloadLocation))
		elif type(link) == type([]):
			self.__links.append(link)


class Link():
	url = ""
	filename = None
	statusCode = None
	filesize = None
	filehash = None
	module = None
	__downloadThread = None
	__downloadLocation = ""
	__status_sema = threading.BoundedSemaphore()
	__status = {
		"speed_curr": "",
		"percent": "0",
		"total_size": "",
		"received": "",
		"speed_avg": "",
		"time_left": ""
	}

	def __init__(self, url, location):
		self.__downloadLocation = location
		self.addUrl(url)

	def getLocation(self):
		return self.__downloadLocation

	def assignDownloadThread(self, thread):
		self.__downloadThread = thread

	def getThread(self):
		return self.__downloadThread

	def getStatus(self):
		if self.__downloadThread is not None:
			with self.__status_sema:
				self.__status = self.__downloadThread.getStatus()
		return self.__status

	def isRunning(self):
		if self.__downloadThread is not None and self.__downloadThread.isAlive():
			return True
		else:
			return False

	def isFinished(self):
		if self.__downloadThread is not None and not self.__downloadThread.isAlive():
			return True
		else:
			return False

	def addUrl(self, url, probe = True):
		if probe:
			probeResult = self.__probeUrl(url)
			self.url = url
			self.filename = probeResult["filename"]
			self.statusCode = probeResult["statusCode"]
			self.filesize = probeResult["filesize"]
			self.filehash = probeResult["hash"]
			self.module = probeResult["module"]

	def __probeUrl(self, url):
		process = subprocess.Popen(["plowprobe", "--no-color", "--printf=\"%c|%f|%h|%m|%s\"", str(url)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		out = ""
		for line in process.stdout:
			out = line.decode()
		if out == "":
			for line in process.stderr:
				out = line.decode()

		parsedOutput = out.replace("\"", "").split("|")

		process.communicate()
		returnCode = process.returncode
		if returnCode == 0:
			return {
						"statusCode": int(parsedOutput[0]),
						"filename": parsedOutput[1],
						"hash": parsedOutput[2],
						"module": parsedOutput[3],
						"filesize": parsedOutput[4],
						"return_code": returnCode
						}
		else:
			return {
							"statusCode": -1,
							"return_code": returnCode
							}


class DownloadThread(threading.Thread):
	url = ""
	__pw = ""
	__username = ""
	__downloadLocation = ""
	__status = {
		"speed_curr": "",
		"percent": "0",
		"total_size": "",
		"received": "",
		"speed_avg": "",
		"time_left": ""
	}

	def __init__(self, linkObj, location, username = "", pw = "", downloadMaxRetries = 3):
		threading.Thread.__init__(self)
		self.__log = logging.getLogger("DownloadThread")
		self.url = linkObj.url
		self.downloadMaxRetries = downloadMaxRetries
		self.__linkObj = linkObj
		self.__downloadLocation = location
		self.setUsername(username)
		self.setPassword(pw)
		self.__status_sema = threading.BoundedSemaphore()

		if not os.path.exists(self.__downloadLocation):
			os.makedirs(self.__downloadLocation)

	def run(self):
		self.download()

	def setPassword(self, pw):
		self.__pw = pw

	def setUsername(self, username):
		self.__username = username

	def getStatus(self):
		with self.__status_sema:
			return self.__status

	def _buildLoginString(self):
		return self.__username + ":" + self.__pw

	def _downloadNeeded(self):
		fsPath = self.__linkObj.getLocation() + "/" + self.__linkObj.filename
		if md5sum(fsPath) == self.__linkObj.filehash:
			self.__log.info("Filehash of {} equals hoster hash for {}".format(fsPath, self.__linkObj.url))
			return False
		else:
			self.__log.info("Downloading {} from {}".format(self.__linkObj.filename, self.__linkObj.url))
			return True

	def download(self):
		fsPath = self.__linkObj.getLocation() + "/" + self.__linkObj.filename
		for retry in range(3):
			if self._downloadNeeded():
				if retry == 0:
					self.__log.info("File does not exist yet. Downloading")
				else:
					self.__log.warn("Checksum does not match up with the hoster reported one. Retry {}".format(retry))
				process = subprocess.Popen(["plowdown", "--no-color", "-m", "-a", self._buildLoginString(), self.url], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.__downloadLocation)
				line = ""
				progress = []
				while process is not None:
					data = process.stderr.read(1)
					line += data.decode("utf-8", "ignore")
					if len(data) > 0:
						if data[0] == 13 or data[0] == 10:
							progress = list(filter(None, line.split(" ")))
							if len(progress) > 6:
								percent = progress[0]
								try:
									percent = int(percent)
								except ValueError: pass
								if (type(percent) == type(0) and percent >= 0 and percent <= 100):
									speed = progress[-1].strip()
									total_size = progress[1].strip()
									received = progress[3].strip()
									speed_avg = progress[6].strip()
									time_left = progress[-2].strip()
									with self.__status_sema:
										self.__status = {
													"speed_curr": speed,
													"percent": percent,
													"total_size": total_size,
													"received": received,
													"speed_avg": speed_avg,
													"time_left": time_left
												}
							line = ""
					else:
						break
			else:
				data = ""
				process = None
				self.__status = {
							"percent": 100,
							"time_left": 0
						}
				break

		self.__log.info("Link {} [{}] done.".format(self.__linkObj.filename, self.__linkObj.url))


def md5sum(filename):
	try:
		with open(filename, mode='rb') as f:
			d = hashlib.md5()
			for buf in iter(partial(f.read, 128), b''):
				d.update(buf)

		return d.hexdigest()
	except FileNotFoundError:
		return None


## EXAMPLE CODE
#import pickle
#
#if os.path.exists("statusDump"):
#	with open("statusDump", "rb") as f:
#		pd = pickle.load(f)
#else:


# vim:ts=2:sts=2:sw=2:noexpandtab
