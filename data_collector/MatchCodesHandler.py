import os.path
from typing import Set, TextIO


class MatchCodesHandler:
    __fileToSaveMatchCodes: TextIO

    def __init__(self):
        self.__fileToSaveMatchCodes: TextIO
        self.__ROOT_DIR = os.path.abspath(os.curdir)

    def saveCodes(self, codesOfMatches: Set[str]):
        """
        Saves match codes used to collect data.

        :param codesOfMatches: List of codes (set to be exact).
        """
        self.__openFile("w")
        stringContent = ";".join(codesOfMatches)
        # Adding last semicolon to separate another set of codes
        stringContent += ";"
        self.__fileToSaveMatchCodes.write(stringContent)
        self.__closeFile()

    def importCodes(self) -> Set[str]:
        """
        Returns all codes from file saved.

        :return: List of codes used to collect data (set to be exact).
        """
        if not os.path.exists(f"{self.__ROOT_DIR}\match_codes.txt"):
            return set()

        self.__openFile("r")
        stringOfCodes = self.__fileToSaveMatchCodes.readline()
        self.__closeFile()
        codesInSet = set(stringOfCodes.split(";"))
        return codesInSet

    def __openFile(self, mode: str):
        """
        Opens a file.

        :param mode: Mode in which file should be opened.
        """
        self.__fileToSaveMatchCodes = open("match_codes.txt", mode)

    def __closeFile(self):
        """
        Closes a file.
        """
        self.__fileToSaveMatchCodes.close()
