#!/usr/bin/python
import re
import sys
import os.path

from definition import *
from string import Template


# Converts text in 'CamelCase' into 'CAMEL_CASE'
# Snippet taken from: http://stackoverflow.com/questions/1175208/elegant-python-function-to-convert-camelcase-to-camel-case
def convertToUnderscore(s):
    return re.sub('(?!^)([0-9A-Z]+)', r'_\1', s).upper().replace('__', '_')


def convertToCamelCase(s):
    result = ''
    begin = True
    for c in s:
        if c == '_':
            begin = True
        elif begin:
            result += c.upper()
            begin = False
        else:
            result += c
    return result


def nameListToString(nameList):
    nameString = ''
    for name in nameList:
        if len(nameString) > 0:
            nameString += ' '
        nameString += name
    return nameString


class MakeSqueakBindingsVisitor:
    def __init__(self, out, apiDefinition):
        self.out = out
        self.variables = {}
        self.constants = {}
        self.typeBindings = {}
        self.interfaceTypeMap = {}

        self.namespacePrefix = apiDefinition.getBindingProperty('Pharo', 'namespacePrefix')
        self.interfaceBaseClassName = self.namespacePrefix + 'Interface'
        self.cbindingsBaseClassName = self.namespacePrefix + 'CBindingsBase'

        self.generatedCodeCategory = apiDefinition.getBindingProperty('Pharo', 'package')
        self.constantsClassName = self.namespacePrefix + 'Constants'
        self.cbindingsClassName = self.namespacePrefix + 'CBindings'
        self.doItClassName = self.namespacePrefix
        self.startedExtensions = set()
        self.externalStructureSuperClass = apiDefinition.getBindingProperty('Squeak', 'externalStructureSuperClass')
        self.externalUnionSuperClass = apiDefinition.getBindingProperty('Squeak', 'externalUnionSuperClass')

    def processText(self, text, **extraVariables):
        t = Template(text)
        return t.substitute(**dict(self.variables.items() + extraVariables.items()))

    def write(self, text):
        self.out.write(text)

    def writeLine(self, line):
        self.write(line)
        self.newline()

    def newline(self):
        self.write('\r')

    def printString(self, text, **extraVariables):
        self.write(self.processText(text, **extraVariables))

    def printLine(self, text, **extraVariables):
        self.write(self.processText(text, **extraVariables))
        self.newline()

    def printDoIt(self, text, **extraVariables):
        self.printLine(text + "!", **extraVariables)

    def visitApiDefinition(self, api):
        self.setup(api)
        self.processVersions(api.versions)
        self.emitBindings(api)

    def setup(self, api):
        self.api = api
        self.variables = {
            'ConstantPrefix': api.constantPrefix,
            'FunctionPrefix': api.functionPrefix,
            'TypePrefix': api.typePrefix,
        }

    def visitEnum(self, enum):
        for constant in enum.constants:
            cname = self.processText("$ConstantPrefix$ConstantName", ConstantName=convertToUnderscore(constant.name))
            self.constants[cname] = constant.value
        cenumName = self.processText("$TypePrefix$EnumName", EnumName=enum.name)
        self.typeBindings[cenumName] = 'long'

    def visitTypedef(self, typedef):
        mappingType = typedef.ctype
        if mappingType.startswith('const '):
            mappingType = mappingType[len('const '):]

        mappingType = mappingType.replace('long', 'longlong').replace('int', 'long').replace('char', 'byte')
        if mappingType.startswith('unsigned '):
            mappingType = 'u' + mappingType[len('unsigned '):]

        if mappingType.startswith('signed '):
            mappingType = mappingType[len('signed '):]

        if mappingType.startswith('uchar') or mappingType.startswith('ubyte'):
            mappingType = mappingType[1:]

        typedefName = self.processText("$TypePrefix$Name", Name=typedef.name)
        self.typeBindings[typedefName] = mappingType

    def visitInterface(self, interface):
        cname = typedefName = self.processText("$TypePrefix$Name", Name=interface.name)
        self.interfaceTypeMap[interface.name + '*'] = self.namespacePrefix + convertToCamelCase(interface.name)
        self.typeBindings[cname] = "void"

    def processFragment(self, fragment):
        # Visit the constants.
        for constant in fragment.constants:
            constant.accept(self)

        # Visit the types.
        for type in fragment.types:
            type.accept(self)

        # Visit the interfaces.
        for interface in fragment.interfaces:
            interface.accept(self)

    def processVersion(self, version):
        self.processFragment(version)

    def processVersions(self, versions):
        for version in versions.values():
            self.processVersion(version)

    def emitClassDefinitionStringList(self, propertyName, stringList):
        
        formattedStringList = ''
        i = 0
        while i < len(stringList):
            space = ''
            if i > 0:
                space = ' '
            formattedStringList += space + stringList[i]
            i += 1

        self.printLine("\t$Property: '$List'", Property=propertyName, List=formattedStringList)

    def emitSubclass(self, baseClass, className, instanceVariableNames=[], classVariableNames=[], poolDictionaries=[]):
        self.printLine('$BaseClass subclass: #$ClassName', BaseClass=baseClass, ClassName=className)
        self.emitClassDefinitionStringList('instanceVariableNames', instanceVariableNames)
        self.emitClassDefinitionStringList('classVariableNames', classVariableNames)
        self.emitClassDefinitionStringList('poolDictionaries', poolDictionaries)
        self.printLine("\tcategory: '$Category'", Category=self.generatedCodeCategory)
        self.printLine('!')

    def emitConstants(self):
        self.emitSubclass('SharedPool', self.constantsClassName, [], list(self.constants.keys()))
        self.beginMethod(self.constantsClassName + ' class', 'initialize', 'initialize')
        self.printLine('"')
        self.printLine('\tself initialize')
        self.printLine('"')
        self.printLine('\tsuper initialize.')
        self.newline()
        self.printString(
"""
    self data pairsDo: [:k :v |
        self classPool at: k put: v
    ]
""")
        self.endMethod()

        self.beginMethod(self.constantsClassName + ' class', 'initialization', 'data')
        self.printLine('\t^ #(')
        for constantName in self.constants.keys():
            constantValue = self.constants[constantName]
            if constantValue.startswith('0x'):
                constantValue = '16r' + constantValue[2:]
            self.printLine("\t\t$ConstantName $ConstantValue", ConstantName=constantName, ConstantValue=constantValue)
        self.printLine('\t)')
        self.endMethod()

    def isValidIdentCharacter(self, character):
        return ('a' <= character and character <= 'z') or \
               ('A' <= character and character <= 'Z') or \
               ('0' <= character and character <= '9') or \
               character == '_'

    def makeFullTypeName(self, rawTypeName):
        baseTypeNameSize = 0
        for i in range(len(rawTypeName)):
            if self.isValidIdentCharacter(rawTypeName[i]):
                baseTypeNameSize = i + 1
            else:
                break

        baseTypeName = rawTypeName[0:baseTypeNameSize]
        typeDecorators = rawTypeName[baseTypeNameSize:]
        mappedBaseType = self.typeBindings[baseTypeName]
        #print "'%s' %d '%s' '%s' -> '%s'" % (rawTypeName, baseTypeNameSize, baseTypeName, typeDecorators, mappedBaseType);
        return mappedBaseType + typeDecorators

    def makeFullTypeNameWithPrefix(self, rawTypeName):
        return self.makeFullTypeName(self.api.typePrefix + rawTypeName)

    def emitTypeBindingsClass(self):
        self.emitSubclass('SharedPool', self.typesClassName, [], list(self.typeBindings.keys()))

    def emitTypeBindings(self):
        self.beginMethod(self.typesClassName + ' class', 'initialize', 'initialize')
        self.printLine('"')
        self.printLine('\tself initialize')
        self.printLine('"')
        self.printLine('\tsuper initialize.')
        self.newline()

        for ctypeName in self.typeBindings.keys():
            SqueakName = self.typeBindings[ctypeName]
            self.printLine('\t$CTypeName := $SqueakName.', CTypeName=ctypeName, SqueakName=SqueakName)
        self.endMethod()

    def emitCBindings(self, api):
        self.emitSubclass(self.cbindingsBaseClassName, self.cbindingsClassName, [], [], [self.constantsClassName])

        for version in api.versions.values():
            # Emit the methods of the interfaces.
            for interface in version.interfaces:
                self.emitInterfaceCBindings(interface)

            # Emit the global c functions
            self.emitCGlobals(version.globals)

    def emitInterfaceCBindings(self, interface):
        for method in interface.methods:
            self.emitCMethodBinding(method, interface.name)

    def emitCGlobals(self, globals):
        for method in globals:
            self.emitCMethodBinding(method, 'global c functions')

    def emitCMethodBinding(self, method, category):

        selector = method.name
        allArguments = method.arguments
        if method.clazz is not None:
            allArguments = [SelfArgument(method.clazz)] + allArguments

        first = True
        for arg in allArguments:
            if first:
                selector += '_'
                first = False
            else:
                selector += ' '

            name = arg.name
            if name == 'self':
                name = 'selfObject'
            selector += name + ': ' + name

        self.beginMethod(self.cbindingsClassName, category, selector)
        self.printString("\t<cdecl: $ReturnType '$FunctionPrefix$FunctionName' (",
            ReturnType=self.makeFullTypeNameWithPrefix(method.returnType),
            FunctionName=method.cname)

        first = True
        for arg in allArguments:
            if first:
                first = False
            else:
                self.printString(' ')

            name = arg.name
            if name == 'self':
                name = 'selfObject'
            argTypeString = arg.type
            if (arg.arrayReturn or arg.pointerList) and argTypeString.endswith('**'):
                argTypeString = argTypeString[:-1]
            self.printString("$ArgType", ArgType=self.makeFullTypeNameWithPrefix(argTypeString), ArgName=name)

        self.printLine(")>")
        self.printLine("^ self externalCallFailed")
        self.endMethod()

    def emitInterfaceClasses(self, api):
        for version in api.versions.values():
            for interface in version.interfaces:
                SqueakName = self.namespacePrefix + convertToCamelCase(interface.name)
                self.emitSubclass(self.interfaceBaseClassName, SqueakName)

    def emitAggregate(self, aggregate):
        cname = self.processText("$TypePrefix$AggregateName", AggregateName=aggregate.name)
        SqueakName = self.namespacePrefix + convertToCamelCase(aggregate.name)
        self.typeBindings[cname] = SqueakName
        superClass = self.externalStructureSuperClass
        if aggregate.isUnion():
            superClass = self.externalUnionSuperClass
        self.emitSubclass(superClass, SqueakName, [], [], [self.constantsClassName])
        self.beginMethod(SqueakName + ' class', 'definition', 'fields')
        self.printLine(
"""	"
	self defineFields
	"
	^ #(""")
        for field in aggregate.fields:
            self.printLine("\t\t($FieldName '$FieldType')", FieldType=self.makeFullTypeNameWithPrefix(field.type), FieldName=field.name)

        self.printLine("\t\t)")
        self.endMethod()

    def emitAggregates(self, api):
        for version in api.versions.values():
            for struct in version.agreggates:
                self.emitAggregate(struct)

    def emitPoolInitializations(self, api, doItClassName):
        self.printDoIt("$Constants initialize.", Constants=self.constantsClassName)

    def emitAggregatesInitializations(self, api, doItClassName):
        for version in api.versions.values():
            for struct in version.agreggates:
                SqueakName = self.namespacePrefix + convertToCamelCase(struct.name)
                self.printDoIt('$Structure defineFields.', Structure=SqueakName)

    def emitDoIts(self, api, doItClassName):
        self.emitPoolInitializations(api, doItClassName)
        self.emitAggregatesInitializations(api, doItClassName)

    def emitBaseClasses(self, api):
        self.emitConstants()
        self.emitInterfaceClasses(api)
        self.emitAggregates(api)
        self.emitCBindings(api)
        self.emitSqueakBindings(api)

        self.emitDoIts(api, self.namespacePrefix + 'GeneratedDoIt')

    def emitSqueakBindings(self, api):
        for version in api.versions.values():
            for interface in version.interfaces:
                self.emitInterfaceBindings(interface)
            self.emitGlobals(version.globals)

    def emitInterfaceBindings(self, interface):
        for method in interface.methods:
            self.emitMethodWrapper(method)

    def emitGlobals(self, globals):
        for method in globals:
            self.emitMethodWrapper(method)

    def emitMethodWrapper(self, method):
        ownerClass = self.namespacePrefix
        clazz = method.clazz
        allArguments = method.arguments
        category = '*' + self.generatedCodeCategory
        if clazz is not None:
            ownerClass = self.namespacePrefix + convertToCamelCase(clazz.name)
            allArguments = [SelfArgument(method.clazz)] + allArguments
            category = 'wrappers'

        methodName = method.name
        if methodName == 'release':
            methodName = 'primitiveRelease'

        # Build the method selector.
        first = True
        for arg in method.arguments:
            name = arg.name
            if first:
                first = False
                methodName += ": " + name
            else:
                methodName += " " + name + ": " + name

        self.beginMethod(ownerClass, category, methodName)

        # Temporal variable for the return value
        self.printLine("\t| resultValue_ |")

        # Call the c bindings.
        self.printString("\tresultValue_ := $CBindingsClass uniqueInstance $MethodName", CBindingsClass=self.cbindingsClassName, MethodName=method.name)
        first = True
        for arg in allArguments:
            name = arg.name
            if name == 'self':
                name = 'selfObject'
            value = name

            if arg.type in self.interfaceTypeMap:
                value = self.processText("(self validHandleOf: $ArgName)", ArgName=name)

            if first:
                if first and clazz is not None:
                    self.printString('_$ArgName: (self validHandle)', ArgName=name)
                else:
                    self.printString('_$ArgName: $ArgValue', ArgName=name, ArgValue=value)
                first = False
            else:
                self.printString(' $ArgName: $ArgValue', ArgName=name, ArgValue=value)
        self.printLine('.')

        if method.returnType in self.interfaceTypeMap:
            self.printLine('\t^ $InterfaceWrapper forHandle: resultValue_', InterfaceWrapper=self.interfaceTypeMap[method.returnType])
        elif method.returnType == 'error':
            self.printLine('\tself checkErrorCode: resultValue_')
        else:
            self.printLine('\t^ resultValue_')

        self.endMethod()

    def emitBindings(self, api):
        self.emitBaseClasses(api)

    def beginMethod(self, className, category, methodHeader):
        self.printLine("!$ClassName methodsFor: '$Category'!", ClassName=className, Category=category)
        self.printLine(methodHeader)

    def endMethod(self):
        self.printLine("! !")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print "make-headers <definitions> <output file>"
    else:
        api = ApiDefinition.loadFromFileNamed(sys.argv[1])
        with open(sys.argv[2], 'w') as out:
            visitor = MakeSqueakBindingsVisitor(out, api)
            api.accept(visitor)