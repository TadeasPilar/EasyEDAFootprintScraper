#!/usr/bin/env python3

import requests
import json
import re
import pcbnew
import os
import shutil
import subprocess
import click
from tempfile import TemporaryDirectory
from pathlib import Path
import parse

class FormatError(RuntimeError):
    pass


class KicadSymbol:
    def __init__(self, raw_data):
        self.data = raw_data.split('\n')

    def write(self, path):
        with open(path, 'w') as file:
            for line in self.data:
                file.write(f"{line}\n")

    def clearEmptySymbol(self):
        for line in self.data:
            if self.countBrackets(line) == 0 and "(symbol " in line:
                print(f"Removing empty symbol line: {line}")
                self.data.remove(line)

    def countBrackets(self, line):
        brackets = 0
        for character in line:
            if character == '(':
                brackets = brackets+1
            if character == ')':
                brackets = brackets-1
        return brackets
    
    def findLine(self, substring):
        for line in self.data:
            if substring in line:
                return line

    def findIncorrectSymbolName(self):
        line = self.findLine("(symbol \"symbol:")
        data = line.split('\"')[1]
        data = data.replace("symbol:", '')
        print(f"Incorrect name is {data}")
        return data

    def rename(self, old, new):
        newData = []
        for line in self.data:
            newData.append(line.replace(old, new))
        self.data = newData

    def getID(self, line):
        iid = parse.parse("{}(id {}){}", line)
        return int(iid[1])

    def findLastProperty(self):
        propertyList = []
        for line in self.data:
            if " (id " in line:
                if self.countBrackets(line) != 0:
                    raise RuntimeError("Property must be oneline!")
                propertyList.append(line)
        m = -1
        lastProp = None
        for prop in propertyList:
            iid = int(self.getID(prop))
            if iid > m-1:
                m = self.getID(prop)
                lastProp = prop
        return lastProp, m

    def generatePropertyString(self, name, value, visible, id):
        hide = ""
        if not visible:
            hide = "hide"
        effects = f"      (effects (font (size 1.27 1.27)) {hide})"
        return f"    (property \"{name}\" \"{value}\" (id {id}) (at 0 0 0)\n{effects}\n    )"

    def findPropertyInsertIndex(self, lastProp):
        if lastProp == None:
            for i in range(len(self.data)):
                if "(symbol \"" in self.data[i]:
                    return int(i+1)
        else:
            return self.data.index(lastProp)+1

    def addProperty(self, name, value, visible):
        lastProp, m = self.findLastProperty()
        propertyString = self.generatePropertyString(name, value, visible, m+1)
        self.data.insert(self.findPropertyInsertIndex(lastProp), propertyString)




def postProcessSymbol(kicadSymbol, componentInfo, footprintLibName):
    partName = componentInfo["title"]

    with open(kicadSymbol, 'r') as file:
        raw_data = file.read()

    symbol = KicadSymbol(raw_data);    
    
    symbol.clearEmptySymbol()
    oldName = symbol.findIncorrectSymbolName()
    symbol.rename(f"symbol:{oldName}", partName)
    symbol.rename(oldName, partName)
    symbol.addProperty("Reference", "NONE?", True)
    symbol.addProperty("Value", partName, True)
    footprint = f"{footprintLibName.split('.')[0]}:{componentInfo['dataStr']['head']['c_para']['package']}"
    symbol.addProperty("Footprint", footprint, False)
    lcsc = componentInfo['lcsc']['number']
    symbol.addProperty("Datasheet", f"https://jlcpcb.com/partdetail/{lcsc}", False)
    symbol.addProperty("LCSC", lcsc, True)
    symbol.addProperty("JLCPCB_CORRECTION", "0;0;0", False)
    symbol.addProperty("PRICE", componentInfo['lcsc']['price'], False)

    return symbol


def easyEdaHeaders(token):
    return {
        'pragma': 'no-cache',
        'cache-control': 'no-cache',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'x-csrf-token': token,
        'x-requested-with': 'XMLHttpRequest',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.116 Safari/537.36',
        'isajax': 'true',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'origin': 'https://easyeda.com',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'cors',
        'sec-fetch-dest': 'empty',
        'referer': 'https://easyeda.com',
        'accept-language': 'cs,en;q=0.9,sk;q=0.8,en-GB;q=0.7',
    }

def extractCsrfToken(pageText):
    m = re.search(r"'X-CSRF-TOKEN':\s*'(.*)'", pageText)
    if not m:
        return None
    return m.group(1)

def obtainCsrfTokenAndCookies():
    homePage = requests.get("https://easyeda.com/")
    return extractCsrfToken(homePage.text), homePage.cookies

def searchComponents(text, token=None, cookies=None):
    """
    Perform fulltext search, return list of components
    """
    if token is None or cookies is None:
        token, cookies = obtainCsrfTokenAndCookies()
    res = requests.post("https://easyeda.com/api/components/search",
        headers=easyEdaHeaders(token), cookies=cookies,
        data={
            "wd": text
        })
    components = []
    lists = res.json()["result"]["lists"]
    if isinstance(lists, list):
        for component in lists:
            components.append(component)
    else:
        for componentList in res.json()["result"]["lists"].values():
            for component in componentList:
                components.append(component)
    return components

def getComponentInfo(lcscCode, token=None, cookies=None):
    for component in searchComponents(lcscCode, token, cookies):
        if component["dataStr"]["head"]["c_para"]["BOM_Supplier Part"] == lcscCode:
            return component

def fetchCompnentDetails(componetUuid, token=None, cookies=None):
    if token is None or cookies is None:
        token, cookies = obtainCsrfTokenAndCookies()
    res = requests.get(f"https://easyeda.com/api/components/{componetUuid}",
        headers=easyEdaHeaders(token), cookies=cookies,
        data={})
    return res.json()["result"]

def getComponentSymbol(componentDetail):
    sch = {
        "editorVersion": "6.4.14",
        "docType": "5",
        "title": "TempSch",
        "description": "",
        "colors": {},
        "schematics": [
        {
            "docType": "1",
            "title": "Sheet_1",
            "description": "",
            "dataStr": {
            "head": {
                "docType": "1",
                "editorVersion": "6.4.14",
                "newgId": True,
                "c_para": {
                "Prefix Start": "1"
                },
                "c_spiceCmd": None
            },
            "colors": {}
            }
        }
        ]
    }
    
    for text in ["canvas", "BBox"]:
        sch["schematics"][0]["dataStr"][text] = componentDetail["dataStr"][text]
    
    shape = "LIB~-5~5~package`" + componentDetail["packageDetail"]["title"] + "`BOM_Supplier`LCSC`BOM_Supplier Part`" + componentDetail["lcsc"]["number"] + "`BOM_Manufacturer`" + componentDetail["dataStr"]["head"]["c_para"]["BOM_Manufacturer"] + "`BOM_Manufacturer Part`" + componentDetail["dataStr"]["head"]["c_para"]["BOM_Manufacturer Part"] + "`Contributor`" + componentDetail["dataStr"]["head"]["c_para"]["Contributor"] + "`spicePre`" + componentDetail["dataStr"]["head"]["c_para"]["pre"][:-1] + "`spiceSymbolName`" + componentDetail["dataStr"]["head"]["c_para"]["name"] + "`~~0~gge03d9a0f3a4a33646~" + componentDetail["uuid"] + "~052918e6192f4a27891c8ca5941aa6aa~0~~yes~yes"
    for line in componentDetail["dataStr"]["shape"]:
        shape += "#@$"
        shape += line
    sch["schematics"][0]["dataStr"]["shape"] = [shape]
    
    return sch

def getComponentPackageName(componentInfo):
    return componentInfo["dataStr"]["head"]["c_para"]["package"]

def getComponentPackage(componentDetail):
    return componentDetail["packageDetail"]

def buildPackageBoard(packageInfo):
    """
    Builds EasyEDA board with a single footprint for footprint extraction
    """
    board = {}
    board["head"] = {
        "docType": "3",
        "editorVersion": "6.4.7",
        "newgId": True,
        "c_para": {},
        "hasIdFlag": True
    }
    for field in ["BBox", "objects", "layers"]:
        board[field] = packageInfo["dataStr"][field]
    x = packageInfo["dataStr"]["head"]["x"]
    y = packageInfo["dataStr"]["head"]["y"]
    shape = f"LIB~{x}~{y}~package`{getComponentPackageName(packageInfo)}`~0~~~1#@$"
    shape += "#@$".join(packageInfo["dataStr"]["shape"])
    board["shape"] = [shape]
    return board

def parseLC2KiCadOutput(text):
    foo = text.decode("utf-8")
    ans_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    data = ans_escape.sub('', foo)
    #print(data)
    for line in data.split('\n'):
        if "Write file" in line:
            data = line.split()
            filename = data[3].replace('\"', '').replace('...', '')
            return filename


def easyEdaToKicad(symbolJson, boardJson, partName, kicadlib):
    """
    Convert board JSON, return pcbnew.BOARD
    """
    with TemporaryDirectory() as tmpDir:
        symbolFilename = os.path.join(tmpDir, "symbol.json")
        boardFilename = os.path.join(tmpDir, "board.json")
        kicadFilename = os.path.join(tmpDir, "board.kicad_pcb")
        with open(symbolFilename, "w") as schFile:
            schFile.write(json.dumps(symbolJson, indent=2))
        with open(boardFilename, "w") as easyFile:
            easyFile.write(json.dumps(boardJson, indent=4))
        subprocess.check_call(["easyeda2kicad", boardFilename, kicadFilename])

        #out = subprocess.check_output(["./LC2KiCad/build/lc2kicad", "-v", symbolFilename, "-a", "ENL:1"], stderr=subprocess.STDOUT)
        
        out = subprocess.check_output(["node", "./easyeda2kicad6/dist/main.js", symbolFilename], stderr=subprocess.STDOUT)
        #symbolName = parseLC2KiCadOutput(out)
        symbolName = f"{tmpDir}/symbol/symbol.kicad_sym"
        print(f"Symbol name: {symbolName}")
        os.rename(symbolName, f"{kicadlib.rsplit('/', 1)[0]}/{partName}.kicad_sym")

        return pcbnew.LoadBoard(kicadFilename), f"{kicadlib.rsplit('/', 1)[0]}/{partName}.kicad_sym"

def validateLibName(lib):
    if not lib.endswith(".pretty"):
        raise FormatError(f"'{lib} is not valid library path, it has to end with '.pretty'")

def ensureKicadLib(lib):
    """
    Ensure the given KiCAD library exists, if not, create it
    """
    if os.path.exists(lib):
        return
    pcbnew.FootprintLibCreate(lib)

def ensure3DLib(lib):
    Path(lib).mkdir(exist_ok=True, parents=True)

def extractFirstFootprint(board):
    for f in board.GetFootprints():
        return f

def topMiddle(rect):
    return pcbnew.wxPoint(rect.GetX() + rect.GetWidth() // 2, rect.GetY())

def bottomMiddle(rect):
    return pcbnew.wxPoint(rect.GetX() + rect.GetWidth() // 2, rect.GetY() + rect.GetHeight())

def postProcessFootprint(footprint):
    footprint.Reference().SetVisible(False)
    footprint.Value().SetVisible(False)

    bbox = footprint.GetBoundingBox(False, False)
    refPos = topMiddle(bbox) + pcbnew.wxPoint(0, -footprint.Reference().GetTextHeight())
    valuePos = bottomMiddle(bbox) + pcbnew.wxPoint(0, +footprint.Reference().GetTextHeight())

    footprint.Reference().SetPosition(refPos)
    footprint.Value().SetPosition(valuePos)

    footprint.Reference().SetVisible(True)
    footprint.Value().SetVisible(True)

def footprintExists(lib, name):
    # PCB_IO().FootprintExists behaves strangely, thus, we implement it ourselves
    return os.path.exists(os.path.join(lib, name + ".kicad_mod"))





def fetchAndConvert(componentInfo, token, cookies, kicadlib):
    uuid = componentInfo["dataStr"]["head"]["uuid"]
    details = fetchCompnentDetails(uuid, token, cookies)
    schSymbol = getComponentSymbol(details)
    package = getComponentPackage(details)
    packageBoard = buildPackageBoard(package)
    kicadBoard, kicadSymbol = easyEdaToKicad(schSymbol, packageBoard, componentInfo["title"], kicadlib)
    footprint = extractFirstFootprint(kicadBoard)
    postProcessFootprint(footprint)
    footprintLibName = kicadlib.split('/')[1]
    symbol = postProcessSymbol(kicadSymbol, componentInfo, footprintLibName)

    return details, footprint, symbol

def fetchAndConvert3D(componentDetail, kicadlib, pathvar, token, cookies):
    lib3D = kicadlib.replace(".pretty", ".3dshapes")
    ensure3DLib(lib3D)

    models = []
    submodels = 0
    for shape in componentDetail["packageDetail"]["dataStr"]["shape"]:
        if not shape.startswith("SVGNODE"):
            continue
        m = re.search(r'"uuid":"([0-9a-f]*)"', shape)
        if not m:
            continue
        shapeUuid = m.group(1)
        res = requests.get(f"https://easyeda.com/analyzer/api/3dmodel/{shapeUuid}",
            headers=easyEdaHeaders(token), cookies=cookies,
            data={})
        packageName = getComponentPackageName(componentDetail)
        if submodels != 0:
            packageName += f"_model_{submodels}"
        objFile = os.path.join(lib3D, packageName + ".obj")
        wrlFile = os.path.join(lib3D, packageName + ".wrl")
        with open(objFile, "w") as f:
            f.write(res.text)
        subprocess.check_call(["ctmconv", objFile, wrlFile])

        desc = pcbnew.FP_3DMODEL()
        desc.m_Filename = "${" + pathvar + "}/" + wrlFile
        desc.m_Scale.x = desc.m_Scale.y = desc.m_Scale.z = 1 / 2.54
        desc.m_Rotation.z = 180
        models.append(desc)

        submodels += 1
    return models


@click.command()
@click.argument("LCSC")
@click.option("--kicadLib", type=click.Path(dir_okay=True, file_okay=False), required=True,
    help="Path to KiCAD library where to store the footprint")
@click.option("--force", is_flag=True,
    help="Overwrite footprint if it already exists in the library")
@click.option("--pathVar", type=str, default="EASY_EDA_3D",
    help="Name of variable, that will be used for prefixing 3D models paths")
def fetchLcsc(kicadlib, force, lcsc, pathvar):
    """
    Fetch a footprint based on LCSC code
    """
    token, cookies = obtainCsrfTokenAndCookies()

    validateLibName(kicadlib)
    ensureKicadLib(kicadlib)

    cinfo = getComponentInfo(lcsc, token, cookies)
    print(f"Component info:{cinfo}")
    if cinfo is None:
        raise RuntimeError(f"No component was found for the code {lcsc}")
    packageName = getComponentPackageName(cinfo)

    if footprintExists(kicadlib, packageName) and not force:
        print(f"Component {lcsc} uses package {packageName} which already exists in {kicadlib}.")
        print(f"Nothing has been done. If you want to overwrite the package, run this command again with '--force'.")
        return

    componentDetail, footprint, symbol = fetchAndConvert(cinfo, token, cookies, kicadlib)
    models = fetchAndConvert3D(componentDetail, kicadlib, pathvar, token, cookies)
    for model in models:
        footprint.Add3DModel(model)

    pcbnew.FootprintSave(kicadlib, footprint)
    symbol.write(f"{kicadlib.split('/')[0]}/{cinfo['title']}.kicad_sym")
    

@click.command()
@click.argument("name")
@click.option("--kicadLib", type=click.Path(dir_okay=True, file_okay=False), required=True,
    help="Path to KiCAD library where to store the footprint")
@click.option("--force",
    help="Overwrite footprint if it already exists in the library")
def fetchName(kicadlib, force, name):
    """
    Fetch a footprint based on full text search.
    """
    pass

@click.group()
def cli():
    """
    Tool for downloading KiCAD footprints from EasyEDA
    """
    pass

cli.add_command(fetchLcsc)
cli.add_command(fetchName)

if __name__ == "__main__":
    cli()
