import dataclasses
from typing import TextIO, List
from model.GameStatsModel import GameStatsModel
import os
import json


class MatchDataSaver:
    __fileToSaveData: TextIO

    def __init__(self):
        self.__fileToSaveData: TextIO
        self.__ROOT_DIR = os.path.abspath(os.curdir)

    def saveData(self, data: List[GameStatsModel]):
        """
        Makes string from list of GameStatsModel and saves it to file.

        :param data: List of GameStatsModel
        """
        self.__openFile("a")
        for game in data:
            # Convert dataclass to dictionary
            gameSerialized = dataclasses.asdict(game)
            # Convert dictionary to string
            gameSerializedAsString = json.dumps(gameSerialized)
            # Adds new line at the end of line to be more readable
            gameSerializedAsString += "\n"
            self.__fileToSaveData.write(gameSerializedAsString)
        self.__closeFile()

    def __openFile(self, mode: str):
        """
        Opens a file.

        :param mode: Mode in which file should be opened.
        """
        self.__fileToSaveData = open("match_data.txt", mode)

    def __closeFile(self):
        """
        Closes a file.
        """
        self.__fileToSaveData.close()
