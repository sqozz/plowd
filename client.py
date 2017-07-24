#!/usr/bin/env python3
import plowd
import logging
import configparser
import sys
import cmd
import argparse
from listcmd import line2argv
import pdb
import os

class AP(argparse.ArgumentParser):
	def exit(self, status = "", message = ""):
		print(message, end="")
		raise Exception

class Client(cmd.Cmd):
	global pd
	intro = "Welcome to the plowd cli client"
	prompt = "plowd-client » "

	parser = AP(description="Created a new container.", prog="")
	subparsers = parser.add_subparsers(help='{sub-command} help')
	parser_container_create = subparsers.add_parser("container_create", help="this sub-command controlls container")
	parser_container_list = subparsers.add_parser("container_list", help="this sub-command lists existing containers")
	parser_exit = subparsers.add_parser("exit", help="exits the cli")

	parser_container_create.add_argument("path", metavar="<Download path>", type=str, help="Downloadpath for the Container")
	parser_container_create.add_argument("name", metavar="<Name>", type=str, help="Name of the new Container")
	parser_container_create.add_argument("linkfile", metavar="<Links File>", type=str, help="Path to the file containing the links")

	def help_container_create(self):
		self.parser_container_create.print_help()

	def help_exit(self):
		self.parser_exit.print_help()

	def do_pdb(self, line):
		pdb.set_trace()

	def do_enable_cnl(self, line):
		pd.startCNLListener()

	def do_container_create(self, line):
		try:
			"creates a container by a given file with links seperated by linebreaks"
			args = self.parser_container_create.parse_args(line2argv(line))
			if args.linkfile is not None and os.path.exists(args.linkfile):
				links = []
				with open(args.linkfile, "r") as f:
					for line in f:
						links += [line.strip()]

				lc = plowd.LinkCollection(args.path, args.name, links)
				pd.addLinkCollection(lc)
			else:
				print("Links file {} not found.".format(args.linkfile))
		except Exception as e:
			print(e)

	def do_container_list(self, line):
		try:
			collections = pd.getCollections()
			print("Total containers: {}".format(len(collections)))
			for collection in collections:
				print("{} -> {}  [{}%]".format(collection.name, collection.getLocation(), collection.getTotalProgress()))
		except Exception as e:
			print(e)

	def do_exit(self, args):
		print("Everything done, going to exit now")
		pd.stopIPC()
		pd.stopDownloader()
		exit()
		return True

	def postcmd(self, stop, line):
		self.lastcmd = ""


if __name__ == "__main__":
	log = logging.getLogger()
	log.setLevel(logging.DEBUG)
	ch = logging.StreamHandler(sys.stdout)
	ch.setLevel(logging.DEBUG)
	formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
	ch.setFormatter(formatter)
	log.addHandler(ch)
	log.info("plowd started!")

	config = configparser.ConfigParser()
	config.read("plowd.ini")
	if "default" in config.sections():
		log.debug("Found section [default] in config. Going to use username and password from it")
		pd = plowd.PlowDown(3, config["default"]["username"], config["default"]["password"])

	pd.startDownloader()

	Client().cmdloop()

# vim:ts=2:sts=2:sw=2:noexpandtab
