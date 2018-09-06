#***************************************************************************
#*                                                                         *
#*   Copyright (c) 2015 - Victor Titov (DeepSOIC)                          *
#*                                               <vv.titov@gmail.com>      *  
#*                                                                         *
#*   This program is free software; you can redistribute it and/or modify  *
#*   it under the terms of the GNU Lesser General Public License (LGPL)    *
#*   as published by the Free Software Foundation; either version 2 of     *
#*   the License, or (at your option) any later version.                   *
#*   for detail see the LICENCE text file.                                 *
#*                                                                         *
#*   This program is distributed in the hope that it will be useful,       *
#*   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#*   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#*   GNU Library General Public License for more details.                  *
#*                                                                         *
#*   You should have received a copy of the GNU Library General Public     *
#*   License along with this program; if not, write to the Free Software   *
#*   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
#*   USA                                                                   *
#*                                                                         *
#***************************************************************************

__title__="Base feature module for lattice object of lattice workbench for FreeCAD"
__author__ = "DeepSOIC"
__url__ = ""

import FreeCAD as App
import Part

from lattice2Common import *
import lattice2CompoundExplorer as LCE
import lattice2Markers
import lattice2Executer
from lattice2ShapeCopy import shallowCopy
import lattice2CoinGlue as CoinGlue


def getDefLatticeFaceColor():
    return (1.0, 0.7019608020782471, 0.0, 0.0) #orange
def getDefShapeColor():
    clr = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/View").GetUnsigned("DefaultShapeColor")
    #convert color in int to color in tuple of 4 floats.
    #This is probably implemented already somewhere, but I couldn't find, so I rolled my own --DeepSOIC
    # clr in hex looks like this: 0xRRGGBBOO (r,g,b,o = red, green, blue, opacity)
    o = clr & 0x000000FF
    b = (clr >> 8) & 0x000000FF
    g = (clr >> 16) & 0x000000FF
    r = (clr >> 24) & 0x000000FF
    return (r/255.0, g/255.0, b/255.0, (255-o)/255.0)
    

def makeLatticeFeature(name, AppClass, ViewClass, no_body = False):
    '''makeLatticeFeature(name, AppClass, ViewClass, no_body = False): makes a document object for a LatticeFeature-derived object.
    
    no_body: if False, the Lattice object will end up in an active body, and Part2DObject will be used.'''
    
    body = activeBody()
    if body and not no_body:
        obj = body.newObject("Part::Part2DObjectPython",name) #hack: body accepts any 2dobjectpython, thinking it is a sketch. Use it to get into body. This does cause some weirdness (e.g. one can Pad a placement), but that is rather minor. 
        obj.AttacherType = 'Attacher::AttachEngine3D'
    else:
        obj = FreeCAD.ActiveDocument.addObject("Part::FeaturePython",name)
    AppClass(obj)
    
    if FreeCAD.GuiUp:
        if ViewClass:
            vp = ViewClass(obj.ViewObject)
        else:
            vp = ViewProviderLatticeFeature(obj.ViewObject)
    return obj
    
    
def isObjectLattice(documentObject):
    '''isObjectLattice(documentObject): When operating on the object, it is to be treated as a lattice object. If False, treat as a regular shape.'''
    transform, src = source(documentObject)
    ret = False
    if hasattr(src,'isLattice'):
        if 'On' in src.isLattice:
            ret = True
    #if documentObject.isDerivedFrom('PartDesign::ShapeBinder'):
    #    if len(documentObject.Support) == 1 and documentObject.Support[0][1] == ('',):
    #        ret = isObjectLattice(documentObject.Support[0][0])
    #if hasattr(documentObject, 'IAm') and documentObject.IAm == 'PartOMagic.Ghost':
    #    ret = isObjectLattice(documentObject.Base)
    return ret
    
def getMarkerSizeEstimate(ListOfPlacements):
    '''getMarkerSizeEstimate(ListOfPlacements): computes the default marker size for the array of placements'''
    if len(ListOfPlacements) == 0:
        return 1.0
    pathLength = 0
    for i in range(1, len(ListOfPlacements)):
        pathLength += (ListOfPlacements[i].Base - ListOfPlacements[i-1].Base).Length
    sz = pathLength/len(ListOfPlacements)/2.0
    #FIXME: make hierarchy-aware
    if sz < DistConfusion*10:
        sz = 1.0
    return sz

    


class LatticeFeature(object):
    "Base object for lattice objects (arrays of placements)"
    
    attachable = False
    
    def __init__(self,obj):
        # please, don't override. Override derivedInit instead.
        obj.addProperty('App::PropertyString', 'Type', "Lattice", "module_name.class_name of this object, for proxy recovery", 0, True, True)
        obj.Type = self.__module__ + '.' + type(self).__name__

        prop = "NumElements"
        obj.addProperty("App::PropertyInteger",prop,"Lattice","Info: number of placements in the array", 0, True)
        obj.setEditorMode(prop, 1) # set read-only
        
        obj.addProperty("App::PropertyLength","MarkerSize","Lattice","Size of placement markers (set to zero for automatic).")
        
        obj.addProperty("App::PropertyEnumeration","MarkerShape","Lattice","Choose the preferred shape of placement markers.")
        obj.MarkerShape = ["tetra-orimarker","paperplane-orimarker"]
        obj.MarkerShape = "paperplane-orimarker" #TODO: setting for choosing the default

        obj.addProperty("App::PropertyEnumeration","isLattice","Lattice","Sets whether this object should be treated as a lattice by further operations")
        obj.isLattice = ['Auto-Off','Auto-On','Force-Off','Force-On']
        # Auto-On an Auto-Off can be modified when recomputing. Force values are going to stay.
        
        prop = "ExposePlacement"
        obj.addProperty("App::PropertyBool",prop,"Lattice","Makes the placement syncronized to Placement property. This will often make this object unmoveable. Not applicable to arrays.")
        
        self.derivedInit(obj)
        self.assureProperties(obj)
        
        self.updateReadonlyness(obj)
        if not self.attachable:
            self.disableAttacher(obj)

        obj.Proxy = self
    
    def assureProperties(self, selfobj):
        """#overrideme Method to reconstruct missing properties, that appeared as new functionality was introduced. 
        Auto called from __init__ (and before derivedInit), and from execute (before derivedExecute)."""
        self.assureProperty(selfobj, 'App::PropertyLink', 'ReferencePlacementLink', None, "Lattice", "Link to placement to use as reference placement")
        self.assureProperty(selfobj, 'App::PropertyString', 'ReferencePlacementLinkIndex', None, "Lattice", "Index of placement to take from the link. Can also be 'self.0' for own placements.")
        self.assureProperty(selfobj, 'App::PropertyBool', 'ReferencePlacementInGlobal', True, "Lattice", "True if reference placement property is in global cs. ", readonly= True)
        self.assureProperty(
            selfobj, 
            'App::PropertyPlacement', 
            'ReferencePlacement', 
            None, 
            "Lattice", 
            "Reference placement, used by 'Populate: build array'. For it, all placements in this array are reinterpreted as relative to this one.", 
            readonly= True
        )

    def updateReadonlyness(self, selfobj, bypass_set = set()):
        is_lattice = isObjectLattice(selfobj)
        extref = 0
        if hasattr(selfobj, 'ReferencePlacementOption'):
            extref = 0 if selfobj.ReferencePlacementOption == 'external' else 1
        rodict = {
            'NumElements': 1, 
            'MarkerSize': 0,
            'MarkerShape': 0,
            'ReferencePlacement': 1,
            'ReferencePlacementLink': extref, 
            'ReferencePlacementLinkIndex': extref,
            'ReferencePlacementInGlobal': 1,
        }
        for prop in rodict:
            if prop in bypass_set: continue
            if hasattr(selfobj, prop):
                selfobj.setEditorMode(prop, rodict[prop] if is_lattice else 2)
        
    def assureProperty(self, selfobj, proptype, propname, defvalue, group, tooltip, readonly = False, hidden = False):
        """assureProperty(selfobj, proptype, propname, defvalue, group, tooltip): adds
        a property if one is missing, and sets its value to default. Does nothing if property 
        already exists. Returns True if property was created, or False if not."""
        
        return assureProperty(selfobj, proptype, propname, defvalue, group, tooltip, readonly, hidden)
    
    def setReferencePlm(self, selfobj, refplm, in_global = False):
        """setReferencePlm(selfobj, refplm, in_global = False): sets reference placement, in internal CS."""
        attr = 'ReferencePlacement'
        if refplm is None:
            refplm = App.Placement()
            in_global = True
    
        if selfobj.ExposePlacement:
            in_global = True
        selfobj.ReferencePlacementInGlobal = in_global
        selfobj.ReferencePlacement = refplm
        
    def getReferencePlm(self, selfobj, in_global = False):
        """getReferencePlm(self, selfobj): Returns reference placement in internal CS, or in global CS."""
        if not hasattr(selfobj, 'ReferencePlacement'):
            return App.Placement() if in_global else selfobj.Placement.inverse()
        if in_global == selfobj.ReferencePlacementInGlobal:
            return selfobj.ReferencePlacement
        elif in_global == True and selfobj.ReferencePlacementInGlobal == False:
            #goal: return == selfobj.Placement * refplm
            return selfobj.Placement.multiply(selfobj.ReferencePlacement)
        elif in_global == False and selfobj.ReferencePlacementInGlobal == True:
            #goal: self.Placement * return == refplm
            return selfobj.Placement.inverse().multiply(selfobj.ReferencePlacement)
    
    def recomputeReferencePlm(self, selfobj, selfplacements):
        lnk = selfobj.ReferencePlacementLink
        strindex = selfobj.ReferencePlacementLinkIndex
        is_selfref = lnk is None and strindex.startswith('self.') 
        ref = selfobj if is_selfref else lnk
        if ref is None:
            self.setReferencePlm(selfobj, None)
        else:
            if is_selfref:
                index = int(strindex[len('self.'):])
            elif len(strindex)>0:
                index = int(strindex)
            else:
                index = 0
            if is_selfref:
                refplm = selfplacements[index]
                self.setReferencePlm(selfobj, refplm, in_global= False)
            else:
                refplm = getPlacementsList(ref)[index]
                self.setReferencePlm(selfobj, refplm, in_global= True)
    
    def derivedInit(self, obj):
        '''for overriding by derived classes'''
        pass
        
    def execute(self,obj):
        # please, don't override. Override derivedExecute instead.
        
        self.assureProperties(obj)

        plms = self.derivedExecute(obj)

        if plms is not None:
            if plms == "suppress":
                return
            obj.NumElements = len(plms)
            shapes = []
            markerSize = obj.MarkerSize
            if markerSize < DistConfusion:
                markerSize = getMarkerSizeEstimate(plms)
            marker = lattice2Markers.getPlacementMarker(scale= markerSize, markerID= obj.MarkerShape)
            self.assureProperty(obj, 'App::PropertyLength', 'MarkerSizeActual', markerSize, "Lattice", "Size of placement markers of this array", hidden= True)
            obj.MarkerSizeActual = markerSize
            
            bExposing = False
            if obj.ExposePlacement:
                if len(plms) == 1:
                    bExposing = True
                else:
                    lattice2Executer.warning(obj,"Multiple placements are being fed, can't expose placements. Placement property will be forced to zero.")
                    obj.Placement = App.Placement()
            
            if bExposing:
                obj.Shape = shallowCopy(marker)
                obj.Placement = plms[0]
            else:
                for plm in plms:
                    sh = shallowCopy(marker)
                    sh.Placement = plm
                    shapes.append(sh)
                    
                if len(shapes) == 0:
                    obj.Shape = lattice2Markers.getNullShapeShape(markerSize)
                    raise ValueError('Lattice object is null') 
                
                sh = Part.makeCompound(shapes)
                sh.Placement = obj.Placement
                obj.Shape = sh

            if obj.isLattice == 'Auto-Off':
                obj.isLattice = 'Auto-On'
            
            self.recomputeReferencePlm(obj, plms)
        else:
            # DerivedExecute didn't return anything. Thus we assume it 
            # has assigned the shape, and thus we don't do anything.
            # Moreover, we assume that it is no longer a lattice object, so:
            if obj.isLattice == 'Auto-On':
                obj.isLattice = 'Auto-Off'
                
            # i don't remember, wtf is going on here...
            if obj.ExposePlacement:
                if obj.Shape.ShapeType == "Compound":
                    children = obj.Shape.childShapes()
                    if len(children) == 1:
                        obj.Placement = children[0].Placement
                        obj.Shape = children[0]
                    else:
                        obj.Placement = App.Placement()
                else:
                    #nothing to do - FreeCAD will take care to make obj.Placement and obj.Shape.Placement synchronized.
                    pass
        self.updateReadonlyness(obj)
    
    def derivedExecute(self,obj):
        '''For overriding by derived class. If this returns a list of placements,
            it's going to be used to build the shape. If returns None, it is assumed that 
            derivedExecute has already assigned the shape, and no further actions are needed. 
            Moreover, None is a signal that the object is not a lattice array, and it will 
            morph into a non-lattice if isLattice is set to auto'''
        return []
                
    def verifyIntegrity(self):
        try:
            if self.__init__.__func__ is not LatticeFeature.__init__.__func__:
                FreeCAD.Console.PrintError("__init__() of lattice object is overridden. Please don't! Fix it!\n")
            if self.execute.__func__ is not LatticeFeature.execute.__func__:
                FreeCAD.Console.PrintError("execute() of lattice object is overridden. Please don't! Fix it!\n")
        except AttributeError as err:
            pass # quick-n-dirty fix for Py3. TODO: restore the functionality in Py3, or remove this routine altogether.
            
    def onChanged(self, obj, prop): #prop is a string - name of the property
        if prop == 'isLattice':
            if obj.ViewObject is not None:
                try:
                    if isObjectLattice(obj):
                        #obj.ViewObject.DisplayMode = 'Shaded'
                        obj.ViewObject.ShapeColor = getDefLatticeFaceColor()
                        obj.ViewObject.Lighting = 'One side'
                    else:
                        #obj.ViewObject.DisplayMode = 'Flat Lines'
                        obj.ViewObject.ShapeColor = getDefShapeColor()
                except App.Base.FreeCADError as err:
                    #these errors pop up while loading project file, apparently because
                    # viewprovider is up already, but the shape vis mesh wasn't yet
                    # created. It is safe to ignore them, as DisplayMode is eventually
                    # restored to the correct values. 
                    #Proper way of dealing with it would have been by testing for 
                    # isRestoring(??), but I failed to find the way to do it.
                    #--DeepSOIC
                    pass 
                    
    def __getstate__(self):
        return None

    def __setstate__(self,state):
        return None
    
    def disableAttacher(self, selfobj, enable= False):
        if selfobj.isDerivedFrom('Part::Part2DObject'):
            attachprops = [
                'Support', 
                'MapMode', 
                'MapReversed', 
                'MapPathParameter', 
                'AttachmentOffset', 
            ]
            for prop in attachprops:
                selfobj.setEditorMode(prop, 0 if enable else 2)
            if enable:
                selfobj.MapMode = selfobj.MapMode #trigger attachment, to make it update property states
    
    def onDocumentRestored(self, selfobj):
        if not self.attachable:
            self.disableAttacher(selfobj)
        self.assureProperties(selfobj)
        self.updateReadonlyness(selfobj)

    
class ViewProviderLatticeFeature(object):
    "A View Provider for base lattice object"

    Object = None # documentobject the vp is attached to 
    ViewObject = None # viewprovider this proxy is attached to
    
    #coin graph:
    #    transform #in sync with Placement
    #    coordinate3 #coordinates for main shape rendering
    #    switch: #main display mode switch, == self.modenode
    #        ...
    #    separator: #reference placement related stuff, == self.refplm_node
    #        transform #reference placement, == self.refplm_tr
    #        separator: #actual shape of reference placement, == self.refplm_sh
    #            ...
    #            switch: #mode switch of reference placement, == self.modenode_refplm
    #                ...
    
    modenode = None # main displaymode switch node
    refplm_node = None # the node containing everything related to reference placement
    refplm_tr = None #transform node of reference placement
    refplm_sh = None #node containing the shape of reference placement
    modenode_refplm = None # displaymode switch node for reference placement

    def __init__(self,vobj):
        '''Don't override. Override derivedInit, please!'''
        vobj.Proxy = self
        vobj.addProperty('App::PropertyString', 'Type', "Lattice", "module_name.class_name of this object, for proxy recovery", 0, True, True)
        vobj.Type = self.__module__ + '.' + type(self).__name__

        
        prop = "DontUnhideOnDelete"
        vobj.addProperty("App::PropertyBool",prop,"Lattice","Makes the element be populated into object's Placement property")
        vobj.setEditorMode(prop, 2) # set hidden
        
        self.derivedInit(vobj)

    def derivedInit(self,vobj):
        pass
       
    def verifyIntegrity(self):
        try:
            if self.__init__.__func__ is not ViewProviderLatticeFeature.__init__.__func__:
                FreeCAD.Console.PrintError("__init__() of lattice object view provider is overridden. Please don't! Fix it!\n")
        except AttributeError as err:
            pass # quick-n-dirty fix for Py3. TODO: restore the functionality in Py3, or remove this routine altogether.
    
    def fixProxy(self, vobj):
        if vobj is not self.ViewObject:
            vobj.Proxy = vobj.ProxyBackup
            raise RuntimeError('fixing broken proxy...')
    
    def getIcon(self):
        return getIconPath("Lattice.svg")

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object
        try:
            vobj.ProxyBackup = self
        except Exception:
            vobj.addProperty('App::PropertyPythonObject', 'ProxyBackup', 'Base', "helper property for workaround FC bug #3564", 0, False, True)
            vobj.ProxyBackup = self
        self.makeRefplmVisual(vobj)
        from pivy import coin
        self.modenode = next((node for node in vobj.RootNode.getChildren() if node.isOfType(coin.SoSwitch.getClassTypeId())))
        
        
    def makeRefplmVisual(self, vobj):
        import pivy
        from pivy import coin
        refplm = self.Object.Proxy.getReferencePlm(self.Object)
        if refplm is not None:
            if not hasattr(self.Object, 'MarkerSizeActual'): return
            if self.refplm_node is None:
                self.refplm_node, self.refplm_tr, self.refplm_sh = lattice2Markers.getRefPlmMarker(self.Object.MarkerShape)
                vobj.RootNode.addChild(self.refplm_node)
                self.modenode_refplm = next((node for node in self.refplm_sh.getChildren() if node.isOfType(coin.SoSwitch.getClassTypeId())))
            CoinGlue.cointransform(refplm, float(self.Object.MarkerSizeActual) * 1.1, self.refplm_tr)
        else:
            if hasattr(self, 'refplm_node') and self.refplm_node is not None:
                vobj.RootNode.removeChild(self.refplm_node)
                self.refplm_node, self.refplm_tr, self.refplm_sh = None, None, None
                self.modenode_refplm = None

    def __getstate__(self):
        return None

    def __setstate__(self,state):
        return None
        
    def claimChildren(self):
        self.Object.Proxy.verifyIntegrity()
        self.verifyIntegrity()
        return []

    def onDelete(self, feature, subelements): # subelements is a tuple of strings
        try:
            if hasattr(self.ViewObject,"DontUnhideOnDelete") and self.ViewObject.DontUnhideOnDelete:
                pass
            else:
                children = self.claimChildren()
                if children and len(children) > 0:
                    marker = lattice2Markers
                    for child in children:
                        child.ViewObject.show()
        except Exception as err:
            # catch all exceptions, because we don't want to prevent deletion if something goes wrong
            FreeCAD.Console.PrintError("Error in onDelete: " + str(err))
        return True
    
    def updateData(self, obj, prop):
        if prop in ['ReferencePlacement', 'MarkerSizeActual', 'Placement', 'ReferencePlacementInGlobal']:
            self.fixProxy(obj.ViewObject)
            self.makeRefplmVisual(obj.ViewObject)
        
    def onChanged(self, vobj, prop):
        if prop == 'Visibility':
            if self.modenode_refplm is not None:
                self.modenode_refplm.whichChild.setValue(0 if vobj.Visibility == True else -1)
        


def assureProperty(docobj, proptype, propname, defvalue, group, tooltip, readonly = False, hidden = False):
    """assureProperty(docobj, proptype, propname, defvalue, group, tooltip, readonly = False, hidden = False): adds
    a property if one is missing, and sets its value to default. Does nothing if property 
    already exists. Returns True if property was created, or False if not."""
    
    if hasattr(docobj, propname):
        #todo: check type match
        return False
        
    docobj.addProperty(proptype, propname, group, tooltip, 0, readonly, hidden)
    if defvalue is not None:
        setattr(docobj, propname, defvalue)
    return True

    
 # ----------------------utility functions -------------------------------------

def makeMoveFromTo(plmFrom, plmTo):
    '''makeMoveFromTo(plmFrom, plmTo): construct a placement that moves something 
    from one placement to another placement'''
    return plmTo.multiply(plmFrom.inverse())

def getPlacementsList(documentObject, context = None, suppressWarning = False):
    '''getPlacementsList(documentObject, context = None): extract list of placements 
    from an array object. Context is an object to report as context, when displaying 
    a warning if the documentObject happens to be a non-lattice.'''
    if not isObjectLattice(documentObject):
        if not suppressWarning:
            lattice2Executer.warning(context, documentObject.Name + " is not a placement or an array of placements. Results may be unexpected.")
    leaves = LCE.AllLeaves(documentObject.Shape)
    return [leaf.Placement for leaf in leaves]

def getReferencePlm(feature):
    """Obtains reference placement, in container's CS (includes feature.Placement)."""
    transform, src = source(feature)
    if not isObjectLattice(src):
        raise TypeError('getReferencePlm: array of placements expected, got something else.')
    return transform.multiply(src.Proxy.getReferencePlm(src))
    
def source(feature):
    """source(feature): finds the original Lattice array feature from behind shapebinders and such. Returns (transform, lattice_feature).
    transform: placement that converts feature's local coordinates into global.
    lattice_feature may not actually be an array of placements."""
    def _source(feature, visitset):
        if hasattr(feature,'isLattice'):
            return (feature.Placement, feature)
        if feature in visitset:
            raise RuntimeError("Dependency loop!")
        visitset.append(feature)
        if feature.isDerivedFrom('PartDesign::ShapeBinder'):
            if len(feature.Support) == 1 and feature.Support[0][1] == ('',):
                base = feature.Support[0][0]
                transform1, src = _source(base, visitset)
                transform = feature.Placement.multiply(base.Placement.inverse().multiply(transform1))
                return (transform, src)
        if hasattr(feature, 'IAm') and feature.IAm == 'PartOMagic.Ghost':
            base = feature.Base
            transform1, src = _source(base, visitset)
            transform = feature.Placement.multiply(base.Placement.inverse().multiply(transform1))
            return (transform, src)
        return (feature.Placement, feature)
    return _source(feature, list())
    

def splitSelection(sel):
    '''splitSelection(sel): splits sel (use getSelectionEx()) into lattices and non-lattices.
    returns a tuple: (lattices, shapes). lattices is a list, containing all objects 
    that are lattices (placements of arrays of placements). shapes contains all 
    the rest. The lists conain SelectionObjects, not the actual document objects.'''
    lattices = []
    shapes = []
    for selobj in sel:
        if isObjectLattice(selobj.Object):
            lattices.append(selobj)
        else:
            shapes.append(selobj)
    return (lattices, shapes)

