#
# This file is part of pyasn1 software.
#
# Copyright (c) 2005-2017, Ilya Etingof <etingof@gmail.com>
# License: http://pyasn1.sf.net/license.html
#
from pyasn1.type import base, tag, univ, char, useful, tagmap
from pyasn1.codec.ber import eoo
from pyasn1.compat.octets import oct2int, octs2ints, isOctetsType, ensureString
from pyasn1.compat.integer import from_bytes
from pyasn1 import debug, error

__all__ = ['decode']


class AbstractDecoder(object):
    protoComponent = None

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        raise error.PyAsn1Error('Decoder not implemented for %s' % (tagSet,))

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        raise error.PyAsn1Error('Indefinite length mode decoder not implemented for %s' % (tagSet,))


class AbstractSimpleDecoder(AbstractDecoder):
    tagFormats = (tag.tagFormatSimple,)

    def _createComponent(self, asn1Spec, tagSet, value=None):
        if tagSet[0].tagFormat not in self.tagFormats:
            raise error.PyAsn1Error('Invalid tag format %s for %s' % (tagSet[0], self.protoComponent.prettyPrintType()))
        if asn1Spec is None:
            return self.protoComponent.clone(value, tagSet)
        elif value is None:
            return asn1Spec
        else:
            return asn1Spec.clone(value)


class AbstractConstructedDecoder(AbstractDecoder):
    tagFormats = (tag.tagFormatConstructed,)

    # noinspection PyUnusedLocal
    def _createComponent(self, asn1Spec, tagSet, value=None):
        if tagSet[0].tagFormat not in self.tagFormats:
            raise error.PyAsn1Error('Invalid tag format %s for %s' % (tagSet[0], self.protoComponent.prettyPrintType()))
        if asn1Spec is None:
            return self.protoComponent.clone(tagSet)
        else:
            return asn1Spec.clone()


class ExplicitTagDecoder(AbstractSimpleDecoder):
    protoComponent = univ.Any('')
    tagFormats = (tag.tagFormatConstructed,)

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        if substrateFun:
            return substrateFun(
                self._createComponent(asn1Spec, tagSet, ''),
                substrate, length
            )
        head, tail = substrate[:length], substrate[length:]
        value, _ = decodeFun(head, asn1Spec, tagSet, length)
        return value, tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        if substrateFun:
            return substrateFun(
                self._createComponent(asn1Spec, tagSet, ''),
                substrate, length
            )
        value, substrate = decodeFun(substrate, asn1Spec, tagSet, length)
        terminator, substrate = decodeFun(substrate, allowEoo=True)
        if eoo.endOfOctets.isSameTypeWith(terminator) and terminator == eoo.endOfOctets:
            return value, substrate
        else:
            raise error.PyAsn1Error('Missing end-of-octets terminator')


explicitTagDecoder = ExplicitTagDecoder()


class IntegerDecoder(AbstractSimpleDecoder):
    protoComponent = univ.Integer(0)

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet, length,
                     state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]

        if not head:
            return self._createComponent(asn1Spec, tagSet, 0), tail

        value = from_bytes(head, signed=True)

        return self._createComponent(asn1Spec, tagSet, value), tail


class BooleanDecoder(IntegerDecoder):
    protoComponent = univ.Boolean(0)

    def _createComponent(self, asn1Spec, tagSet, value=None):
        return IntegerDecoder._createComponent(self, asn1Spec, tagSet, value and 1 or 0)


class BitStringDecoder(AbstractSimpleDecoder):
    protoComponent = univ.BitString(())
    tagFormats = (tag.tagFormatSimple, tag.tagFormatConstructed)
    supportConstructedForm = True

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet, length,
                     state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        if tagSet[0].tagFormat == tag.tagFormatSimple:  # XXX what tag to check?
            if not head:
                raise error.PyAsn1Error('Empty substrate')
            trailingBits = oct2int(head[0])
            if trailingBits > 7:
                raise error.PyAsn1Error(
                    'Trailing bits overflow %s' % trailingBits
                )
            head = head[1:]
            value = self.protoComponent.fromOctetString(head, trailingBits)
            return self._createComponent(asn1Spec, tagSet, value), tail

        if not self.supportConstructedForm:
            raise error.PyAsn1Error('Constructed encoding form prohibited at %s' % self.__class__.__name__)

        bitString = self._createComponent(asn1Spec, tagSet)

        if substrateFun:
            return substrateFun(bitString, substrate, length)

        while head:
            component, head = decodeFun(head, self.protoComponent)
            bitString += component

        return bitString, tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        bitString = self._createComponent(asn1Spec, tagSet)

        if substrateFun:
            return substrateFun(bitString, substrate, length)

        while substrate:
            component, substrate = decodeFun(substrate, self.protoComponent, allowEoo=True)
            if eoo.endOfOctets.isSameTypeWith(component) and component == eoo.endOfOctets:
                break

            bitString += component

        else:
            raise error.SubstrateUnderrunError('No EOO seen before substrate ends')

        return bitString, substrate


class OctetStringDecoder(AbstractSimpleDecoder):
    protoComponent = univ.OctetString('')
    tagFormats = (tag.tagFormatSimple, tag.tagFormatConstructed)
    supportConstructedForm = True

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet, length,
                     state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        if tagSet[0].tagFormat == tag.tagFormatSimple:  # XXX what tag to check?
            return self._createComponent(asn1Spec, tagSet, head), tail
        if not self.supportConstructedForm:
            raise error.PyAsn1Error('Constructed encoding form prohibited at %s' % self.__class__.__name__)
        asn1Object = self._createComponent(asn1Spec, tagSet, '')
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        while head:
            component, head = decodeFun(head, self.protoComponent)
            asn1Object += component
        return asn1Object, tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        asn1Object = self._createComponent(asn1Spec, tagSet, '')
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        while substrate:
            component, substrate = decodeFun(substrate, self.protoComponent,
                                             allowEoo=True)
            if eoo.endOfOctets.isSameTypeWith(component) and component == eoo.endOfOctets:
                break
            asn1Object += component
        else:
            raise error.SubstrateUnderrunError(
                'No EOO seen before substrate ends'
            )
        return asn1Object, substrate


class NullDecoder(AbstractSimpleDecoder):
    protoComponent = univ.Null('')

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        r = self._createComponent(asn1Spec, tagSet)
        if head:
            raise error.PyAsn1Error('Unexpected %d-octet substrate for Null' % length)
        return r, tail


class ObjectIdentifierDecoder(AbstractSimpleDecoder):
    protoComponent = univ.ObjectIdentifier(())

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet, length,
                     state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        if not head:
            raise error.PyAsn1Error('Empty substrate')

        head = octs2ints(head)

        oid = ()
        index = 0
        substrateLen = len(head)
        while index < substrateLen:
            subId = head[index]
            index += 1
            if subId < 128:
                oid = oid + (subId,)
            elif subId > 128:
                # Construct subid from a number of octets
                nextSubId = subId
                subId = 0
                while nextSubId >= 128:
                    subId = (subId << 7) + (nextSubId & 0x7F)
                    if index >= substrateLen:
                        raise error.SubstrateUnderrunError(
                            'Short substrate for sub-OID past %s' % (oid,)
                        )
                    nextSubId = head[index]
                    index += 1
                oid += ((subId << 7) + nextSubId,)
            elif subId == 128:
                # ASN.1 spec forbids leading zeros (0x80) in OID
                # encoding, tolerating it opens a vulnerability. See
                # http://www.cosic.esat.kuleuven.be/publications/article-1432.pdf
                # page 7
                raise error.PyAsn1Error('Invalid octet 0x80 in OID encoding')

        # Decode two leading arcs
        if 0 <= oid[0] <= 39:
            oid = (0,) + oid
        elif 40 <= oid[0] <= 79:
            oid = (1, oid[0] - 40) + oid[1:]
        elif oid[0] >= 80:
            oid = (2, oid[0] - 80) + oid[1:]
        else:
            raise error.PyAsn1Error('Malformed first OID octet: %s' % head[0])

        return self._createComponent(asn1Spec, tagSet, oid), tail


class RealDecoder(AbstractSimpleDecoder):
    protoComponent = univ.Real()

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        if not head:
            return self._createComponent(asn1Spec, tagSet, 0.0), tail
        fo = oct2int(head[0])
        head = head[1:]
        if fo & 0x80:  # binary encoding
            if not head:
                raise error.PyAsn1Error("Incomplete floating-point value")
            n = (fo & 0x03) + 1
            if n == 4:
                n = oct2int(head[0])
                head = head[1:]
            eo, head = head[:n], head[n:]
            if not eo or not head:
                raise error.PyAsn1Error('Real exponent screwed')
            e = oct2int(eo[0]) & 0x80 and -1 or 0
            while eo:  # exponent
                e <<= 8
                e |= oct2int(eo[0])
                eo = eo[1:]
            b = fo >> 4 & 0x03  # base bits
            if b > 2:
                raise error.PyAsn1Error('Illegal Real base')
            if b == 1:  # encbase = 8
                e *= 3
            elif b == 2:  # encbase = 16
                e *= 4
            p = 0
            while head:  # value
                p <<= 8
                p |= oct2int(head[0])
                head = head[1:]
            if fo & 0x40:  # sign bit
                p = -p
            sf = fo >> 2 & 0x03  # scale bits
            p *= 2 ** sf
            value = (p, 2, e)
        elif fo & 0x40:  # infinite value
            value = fo & 0x01 and '-inf' or 'inf'
        elif fo & 0xc0 == 0:  # character encoding
            if not head:
                raise error.PyAsn1Error("Incomplete floating-point value")
            try:
                if fo & 0x3 == 0x1:  # NR1
                    value = (int(head), 10, 0)
                elif fo & 0x3 == 0x2:  # NR2
                    value = float(head)
                elif fo & 0x3 == 0x3:  # NR3
                    value = float(head)
                else:
                    raise error.SubstrateUnderrunError(
                        'Unknown NR (tag %s)' % fo
                    )
            except ValueError:
                raise error.SubstrateUnderrunError(
                    'Bad character Real syntax'
                )
        else:
            raise error.SubstrateUnderrunError(
                'Unknown encoding (tag %s)' % fo
            )
        return self._createComponent(asn1Spec, tagSet, value), tail


class SequenceAndSetDecoderBase(AbstractConstructedDecoder):
    protoComponent = None
    orderedComponents = False

    def _getComponentTagMap(self, asn1Object, idx):
        raise NotImplementedError()

    def _getComponentPositionByType(self, asn1Object, tagSet, idx):
        raise NotImplementedError()

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        asn1Object = self._createComponent(asn1Spec, tagSet)
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)

        namedTypes = asn1Object.getComponentType()

        if not self.orderedComponents or not namedTypes or namedTypes.hasOptionalOrDefault:
            idx = 0
            while head:
                asn1Spec = self._getComponentTagMap(asn1Object, idx)
                component, head = decodeFun(head, asn1Spec)
                idx = self._getComponentPositionByType(
                    asn1Object, component.getEffectiveTagSet(), idx
                )

                asn1Object.setComponentByPosition(
                    idx, component,
                    verifyConstraints=False,
                    matchTags=False, matchConstraints=False
                )
                idx += 1

            asn1Object.setDefaultComponents()
        else:
            for idx, asn1Spec in enumerate(namedTypes.values()):
                component, head = decodeFun(head, asn1Spec)
                asn1Object.setComponentByPosition(
                    idx, component,
                    verifyConstraints=False,
                    matchTags=False, matchConstraints=False
                )

        if not namedTypes:
            asn1Object.verifySizeSpec()

        return asn1Object, tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        asn1Object = self._createComponent(asn1Spec, tagSet)
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)

        namedTypes = asn1Object.getComponentType()

        if not namedTypes or namedTypes.hasOptionalOrDefault:
            idx = 0
            while substrate:
                asn1Spec = self._getComponentTagMap(asn1Object, idx)
                component, substrate = decodeFun(substrate, asn1Spec, allowEoo=True)
                if eoo.endOfOctets.isSameTypeWith(component) and component == eoo.endOfOctets:
                    break
                idx = self._getComponentPositionByType(
                    asn1Object, component.getEffectiveTagSet(), idx
                )

                asn1Object.setComponentByPosition(
                    idx, component,
                    verifyConstraints=False,
                    matchTags=False, matchConstraints=False
                )
                idx += 1

            else:
                raise error.SubstrateUnderrunError(
                    'No EOO seen before substrate ends'
                )

            asn1Object.setDefaultComponents()
        else:
            for idx, asn1Spec in enumerate(namedTypes.values()):
                component, substrate = decodeFun(substrate, asn1Spec)

                asn1Object.setComponentByPosition(
                    idx, component,
                    verifyConstraints=False,
                    matchTags=False, matchConstraints=False
                )

            component, substrate = decodeFun(substrate, eoo.endOfOctets, allowEoo=True)
            if not eoo.endOfOctets.isSameTypeWith(component) or component != eoo.endOfOctets:
                raise error.SubstrateUnderrunError(
                    'No EOO seen before substrate ends'
                )

        if not namedTypes:
            asn1Object.verifySizeSpec()

        return asn1Object, substrate

class SequenceDecoder(SequenceAndSetDecoderBase):
    protoComponent = univ.Sequence()
    orderedComponents = True

    def _getComponentTagMap(self, asn1Object, idx):
        try:
            return asn1Object.getComponentTagMapNearPosition(idx)
        except error.PyAsn1Error:
            return

    def _getComponentPositionByType(self, asn1Object, tagSet, idx):
        return asn1Object.getComponentPositionNearType(tagSet, idx)


class SequenceOfDecoder(AbstractConstructedDecoder):
    protoComponent = univ.SequenceOf()

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        asn1Object = self._createComponent(asn1Spec, tagSet)
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        asn1Spec = asn1Object.getComponentType()
        idx = 0
        while head:
            component, head = decodeFun(head, asn1Spec)
            asn1Object.setComponentByPosition(
                idx, component,
                verifyConstraints=False,
                matchTags=False, matchConstraints=False
            )
            idx += 1
        asn1Object.verifySizeSpec()
        return asn1Object, tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        asn1Object = self._createComponent(asn1Spec, tagSet)
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        asn1Spec = asn1Object.getComponentType()
        idx = 0
        while substrate:
            component, substrate = decodeFun(substrate, asn1Spec, allowEoo=True)
            if eoo.endOfOctets.isSameTypeWith(component) and component == eoo.endOfOctets:
                break
            asn1Object.setComponentByPosition(
                idx, component,
                verifyConstraints=False,
                matchTags=False, matchConstraints=False
            )
            idx += 1
        else:
            raise error.SubstrateUnderrunError(
                'No EOO seen before substrate ends'
            )
        asn1Object.verifySizeSpec()
        return asn1Object, substrate


class SetDecoder(SequenceAndSetDecoderBase):
    protoComponent = univ.Set()
    orderedComponents = False

    def _getComponentTagMap(self, asn1Object, idx):
        return asn1Object.getComponentTagMap()

    def _getComponentPositionByType(self, asn1Object, tagSet, idx):
        nextIdx = asn1Object.getComponentPositionByType(tagSet)
        if nextIdx is None:
            return idx
        else:
            return nextIdx


class SetOfDecoder(SequenceOfDecoder):
    protoComponent = univ.SetOf()


class ChoiceDecoder(AbstractConstructedDecoder):
    protoComponent = univ.Choice()
    tagFormats = (tag.tagFormatSimple, tag.tagFormatConstructed)

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        head, tail = substrate[:length], substrate[length:]
        asn1Object = self._createComponent(asn1Spec, tagSet)
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        if asn1Object.getTagSet() == tagSet:  # explicitly tagged Choice
            component, head = decodeFun(
                head, asn1Object.getComponentTagMap()
            )
        else:
            component, head = decodeFun(
                head, asn1Object.getComponentTagMap(), tagSet, length, state
            )
        effectiveTagSet = component.getEffectiveTagSet()
        asn1Object.setComponentByType(
            effectiveTagSet, component,
            verifyConstraints=False,
            matchTags=False, matchConstraints=False,
            innerFlag=False
        )
        return asn1Object, tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        asn1Object = self._createComponent(asn1Spec, tagSet)
        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        if asn1Object.getTagSet() == tagSet:  # explicitly tagged Choice
            component, substrate = decodeFun(substrate, asn1Object.getComponentTagMap())
            # eat up EOO marker
            eooMarker, substrate = decodeFun(substrate, allowEoo=True)
            if not eoo.endOfOctets.isSameTypeWith(eooMarker) or eooMarker != eoo.endOfOctets:
                raise error.PyAsn1Error('No EOO seen before substrate ends')
        else:
            component, substrate = decodeFun(
                substrate, asn1Object.getComponentTagMap(), tagSet, length, state
            )
        effectiveTagSet = component.getEffectiveTagSet()
        asn1Object.setComponentByType(
            effectiveTagSet, component,
            verifyConstraints=False,
            matchTags=False, matchConstraints=False,
            innerFlag=False
        )
        return asn1Object, substrate


class AnyDecoder(AbstractSimpleDecoder):
    protoComponent = univ.Any()
    tagFormats = (tag.tagFormatSimple, tag.tagFormatConstructed)

    def valueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                     length, state, decodeFun, substrateFun):
        if asn1Spec is None or asn1Spec is not None and tagSet != asn1Spec.getTagSet():
            # untagged Any container, recover inner header substrate
            length = length + len(fullSubstrate) - len(substrate)
            substrate = fullSubstrate
        if substrateFun:
            return substrateFun(self._createComponent(asn1Spec, tagSet),
                                substrate, length)
        head, tail = substrate[:length], substrate[length:]
        return self._createComponent(asn1Spec, tagSet, value=head), tail

    def indefLenValueDecoder(self, fullSubstrate, substrate, asn1Spec, tagSet,
                             length, state, decodeFun, substrateFun):
        if asn1Spec is not None and tagSet == asn1Spec.getTagSet():
            # tagged Any type -- consume header substrate
            header = ''
        else:
            # untagged Any, recover header substrate
            header = fullSubstrate[:-len(substrate)]

        asn1Object = self._createComponent(asn1Spec, tagSet, header)

        # Any components do not inherit initial tag
        asn1Spec = self.protoComponent

        if substrateFun:
            return substrateFun(asn1Object, substrate, length)
        while substrate:
            component, substrate = decodeFun(substrate, asn1Spec, allowEoo=True)
            if eoo.endOfOctets.isSameTypeWith(component) and component == eoo.endOfOctets:
                break
            asn1Object = asn1Object + component
        else:
            raise error.SubstrateUnderrunError(
                'No EOO seen before substrate ends'
            )
        return asn1Object, substrate


# character string types
class UTF8StringDecoder(OctetStringDecoder):
    protoComponent = char.UTF8String()


class NumericStringDecoder(OctetStringDecoder):
    protoComponent = char.NumericString()


class PrintableStringDecoder(OctetStringDecoder):
    protoComponent = char.PrintableString()


class TeletexStringDecoder(OctetStringDecoder):
    protoComponent = char.TeletexString()


class VideotexStringDecoder(OctetStringDecoder):
    protoComponent = char.VideotexString()


class IA5StringDecoder(OctetStringDecoder):
    protoComponent = char.IA5String()


class GraphicStringDecoder(OctetStringDecoder):
    protoComponent = char.GraphicString()


class VisibleStringDecoder(OctetStringDecoder):
    protoComponent = char.VisibleString()


class GeneralStringDecoder(OctetStringDecoder):
    protoComponent = char.GeneralString()


class UniversalStringDecoder(OctetStringDecoder):
    protoComponent = char.UniversalString()


class BMPStringDecoder(OctetStringDecoder):
    protoComponent = char.BMPString()


# "useful" types
class ObjectDescriptorDecoder(OctetStringDecoder):
    protoComponent = useful.ObjectDescriptor()


class GeneralizedTimeDecoder(OctetStringDecoder):
    protoComponent = useful.GeneralizedTime()


class UTCTimeDecoder(OctetStringDecoder):
    protoComponent = useful.UTCTime()


tagMap = {
    univ.Integer.tagSet: IntegerDecoder(),
    univ.Boolean.tagSet: BooleanDecoder(),
    univ.BitString.tagSet: BitStringDecoder(),
    univ.OctetString.tagSet: OctetStringDecoder(),
    univ.Null.tagSet: NullDecoder(),
    univ.ObjectIdentifier.tagSet: ObjectIdentifierDecoder(),
    univ.Enumerated.tagSet: IntegerDecoder(),
    univ.Real.tagSet: RealDecoder(),
    univ.Sequence.tagSet: SequenceDecoder(),  # conflicts with SequenceOf
    univ.Set.tagSet: SetDecoder(),  # conflicts with SetOf
    univ.Choice.tagSet: ChoiceDecoder(),  # conflicts with Any
    # character string types
    char.UTF8String.tagSet: UTF8StringDecoder(),
    char.NumericString.tagSet: NumericStringDecoder(),
    char.PrintableString.tagSet: PrintableStringDecoder(),
    char.TeletexString.tagSet: TeletexStringDecoder(),
    char.VideotexString.tagSet: VideotexStringDecoder(),
    char.IA5String.tagSet: IA5StringDecoder(),
    char.GraphicString.tagSet: GraphicStringDecoder(),
    char.VisibleString.tagSet: VisibleStringDecoder(),
    char.GeneralString.tagSet: GeneralStringDecoder(),
    char.UniversalString.tagSet: UniversalStringDecoder(),
    char.BMPString.tagSet: BMPStringDecoder(),
    # useful types
    useful.ObjectDescriptor.tagSet: ObjectDescriptorDecoder(),
    useful.GeneralizedTime.tagSet: GeneralizedTimeDecoder(),
    useful.UTCTime.tagSet: UTCTimeDecoder()
}

# Type-to-codec map for ambiguous ASN.1 types
typeMap = {
    univ.Set.typeId: SetDecoder(),
    univ.SetOf.typeId: SetOfDecoder(),
    univ.Sequence.typeId: SequenceDecoder(),
    univ.SequenceOf.typeId: SequenceOfDecoder(),
    univ.Choice.typeId: ChoiceDecoder(),
    univ.Any.typeId: AnyDecoder()
}

(stDecodeTag, stDecodeLength, stGetValueDecoder, stGetValueDecoderByAsn1Spec,
 stGetValueDecoderByTag, stTryAsExplicitTag, stDecodeValue,
 stDumpRawValue, stErrorCondition, stStop) = [x for x in range(10)]


class Decoder(object):
    defaultErrorState = stErrorCondition
    #    defaultErrorState = stDumpRawValue
    defaultRawDecoder = AnyDecoder()
    supportIndefLength = True

    # noinspection PyDefaultArgument
    def __init__(self, tagMap, typeMap={}):
        self.__tagMap = tagMap
        self.__typeMap = typeMap
        # Tag & TagSet objects caches
        self.__tagCache = {}
        self.__tagSetCache = {}

    def __call__(self, substrate, asn1Spec=None, tagSet=None,
                 length=None, state=stDecodeTag, recursiveFlag=1,
                 substrateFun=None, allowEoo=False):
        if debug.logger & debug.flagDecoder:
            debug.logger('decoder called at scope %s with state %d, working with up to %d octets of substrate: %s' % (debug.scope, state, len(substrate), debug.hexdump(substrate)))

        substrate = ensureString(substrate)
        value = base.noValue

        fullSubstrate = substrate
        while state != stStop:
            if state == stDecodeTag:
                if not substrate:
                    raise error.SubstrateUnderrunError(
                        'Short octet stream on tag decoding'
                    )
                # Decode tag
                firstOctet = substrate[0]
                substrate = substrate[1:]
                try:
                    lastTag = self.__tagCache[firstOctet]
                except KeyError:
                    firstOctet = oct2int(firstOctet)
                    # Look for end-of-octets sentinel
                    if firstOctet == 0:
                        if substrate and oct2int(substrate[0]) == 0:
                            if allowEoo and self.supportIndefLength:
                                debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                                    'end-of-octets sentinel found')
                                value, substrate = eoo.endOfOctets, substrate[1:]
                                state = stStop
                                continue
                            else:
                                raise error.PyAsn1Error('Unexpected end-of-contents sentinel')
                        else:
                            raise error.PyAsn1Error('Zero tag encountered')
                    tagClass = firstOctet & 0xC0
                    tagFormat = firstOctet & 0x20
                    tagId = firstOctet & 0x1F
                    isShortTag = True
                    if tagId == 0x1F:
                        isShortTag = False
                        lengthOctetIdx = 0
                        tagId = 0
                        try:
                            while True:
                                firstOctet = oct2int(substrate[lengthOctetIdx])
                                lengthOctetIdx += 1
                                tagId <<= 7 | (firstOctet & 0x7F)
                                if not firstOctet & 0x80:
                                    break
                            substrate = substrate[lengthOctetIdx:]
                        except IndexError:
                            raise error.SubstrateUnderrunError(
                                'Short octet stream on long tag decoding'
                            )
                    lastTag = tag.Tag(
                        tagClass=tagClass, tagFormat=tagFormat, tagId=tagId
                    )
                    if isShortTag:
                        # cache short tags
                        self.__tagCache[firstOctet] = lastTag
                if tagSet is None:
                    try:
                        tagSet = self.__tagSetCache[firstOctet]
                    except KeyError:
                        # base tag not recovered
                        tagSet = tag.TagSet((), lastTag)
                        if firstOctet in self.__tagCache:
                            self.__tagSetCache[firstOctet] = tagSet
                else:
                    tagSet = lastTag + tagSet
                state = stDecodeLength
                debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                    'tag decoded into %s, decoding length' % tagSet)
            if state == stDecodeLength:
                # Decode length
                if not substrate:
                    raise error.SubstrateUnderrunError(
                        'Short octet stream on length decoding'
                    )
                firstOctet = oct2int(substrate[0])
                if firstOctet < 128:
                    size = 1
                    length = firstOctet
                elif firstOctet == 128:
                    size = 1
                    length = -1
                else:
                    size = firstOctet & 0x7F
                    # encoded in size bytes
                    encodedLength = octs2ints(substrate[1:size + 1])
                    # missing check on maximum size, which shouldn't be a
                    # problem, we can handle more than is possible
                    if len(encodedLength) != size:
                        raise error.SubstrateUnderrunError(
                            '%s<%s at %s' % (size, len(encodedLength), tagSet)
                        )
                    length = 0
                    for lengthOctet in encodedLength:
                        length <<= 8
                        length |= lengthOctet
                    size += 1
                substrate = substrate[size:]
                if length == -1:
                    if not self.supportIndefLength:
                        raise error.PyAsn1Error('Indefinite length encoding not supported by this codec')
                else:
                    if len(substrate) < length:
                        raise error.SubstrateUnderrunError('%d-octet short' % (length - len(substrate)))
                state = stGetValueDecoder
                debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                    'value length decoded into %d, payload substrate is: %s' % (length, debug.hexdump(length == -1 and substrate or substrate[:length]))
                )
            if state == stGetValueDecoder:
                if asn1Spec is None:
                    state = stGetValueDecoderByTag
                else:
                    state = stGetValueDecoderByAsn1Spec
            #
            # There're two ways of creating subtypes in ASN.1 what influences
            # decoder operation. These methods are:
            # 1) Either base types used in or no IMPLICIT tagging has been
            #    applied on subtyping.
            # 2) Subtype syntax drops base type information (by means of
            #    IMPLICIT tagging.
            # The first case allows for complete tag recovery from substrate
            # while the second one requires original ASN.1 type spec for
            # decoding.
            #
            # In either case a set of tags (tagSet) is coming from substrate
            # in an incremental, tag-by-tag fashion (this is the case of
            # EXPLICIT tag which is most basic). Outermost tag comes first
            # from the wire.
            #            
            if state == stGetValueDecoderByTag:
                try:
                    concreteDecoder = self.__tagMap[tagSet]
                except KeyError:
                    concreteDecoder = None
                if concreteDecoder:
                    state = stDecodeValue
                else:
                    try:
                        concreteDecoder = self.__tagMap[tagSet[:1]]
                    except KeyError:
                        concreteDecoder = None
                    if concreteDecoder:
                        state = stDecodeValue
                    else:
                        state = stTryAsExplicitTag
                if debug.logger and debug.logger & debug.flagDecoder:
                    debug.logger('codec %s chosen by a built-in type, decoding %s' % (concreteDecoder and concreteDecoder.__class__.__name__ or "<none>", state == stDecodeValue and 'value' or 'as explicit tag'))
                    debug.scope.push(
                        concreteDecoder is None and '?' or concreteDecoder.protoComponent.__class__.__name__)
            if state == stGetValueDecoderByAsn1Spec:
                if asn1Spec.__class__ is dict or asn1Spec.__class__ is tagmap.TagMap:
                    try:
                        chosenSpec = asn1Spec[tagSet]
                    except KeyError:
                        chosenSpec = None
                    if debug.logger and debug.logger & debug.flagDecoder:
                        debug.logger('candidate ASN.1 spec is a map of:')
                        for firstOctet, v in asn1Spec.posMap.items():
                            debug.logger('  %s -> %s' % (firstOctet, v.__class__.__name__))
                        if asn1Spec.negMap:
                            debug.logger('but neither of: ')
                            for firstOctet, v in asn1Spec.negMap.items():
                                debug.logger('  %s -> %s' % (firstOctet, v.__class__.__name__))
                        debug.logger('new candidate ASN.1 spec is %s, chosen by %s' % (chosenSpec is None and '<none>' or chosenSpec.prettyPrintType(), tagSet))
                else:
                    chosenSpec = asn1Spec
                    debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                        'candidate ASN.1 spec is %s' % asn1Spec.__class__.__name__)
                if chosenSpec is not None and (tagSet == chosenSpec.getTagSet() or tagSet in chosenSpec.getTagMap()):
                    # use base type for codec lookup to recover untagged types
                    baseTagSet = chosenSpec.baseTagSet
                    try:
                        # ambiguous type
                        concreteDecoder = self.__typeMap[chosenSpec.typeId]
                        debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                            'value decoder chosen for an ambiguous type by type ID %s' % (chosenSpec.typeId,))
                    except KeyError:
                        try:
                            # base type or tagged subtype
                            concreteDecoder = self.__tagMap[baseTagSet]
                            debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                                'value decoder chosen by base %s' % (baseTagSet,))
                        except KeyError:
                            concreteDecoder = None
                    if concreteDecoder:
                        asn1Spec = chosenSpec
                        state = stDecodeValue
                    else:
                        state = stTryAsExplicitTag
                else:
                    concreteDecoder = None
                    state = stTryAsExplicitTag
                if debug.logger and debug.logger & debug.flagDecoder:
                    debug.logger('codec %s chosen by ASN.1 spec, decoding %s' % (state == stDecodeValue and concreteDecoder.__class__.__name__ or "<none>", state == stDecodeValue and 'value' or 'as explicit tag'))
                    debug.scope.push(chosenSpec is None and '?' or chosenSpec.__class__.__name__)
            if state == stTryAsExplicitTag:
                if tagSet and tagSet[0].tagFormat == tag.tagFormatConstructed and tagSet[0].tagClass != tag.tagClassUniversal:
                    # Assume explicit tagging
                    concreteDecoder = explicitTagDecoder
                    state = stDecodeValue
                else:
                    concreteDecoder = None
                    state = self.defaultErrorState
                debug.logger and debug.logger & debug.flagDecoder and debug.logger('codec %s chosen, decoding %s' % (concreteDecoder and concreteDecoder.__class__.__name__ or "<none>", state == stDecodeValue and 'value' or 'as failure'))
            if state == stDumpRawValue:
                concreteDecoder = self.defaultRawDecoder
                debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                    'codec %s chosen, decoding value' % concreteDecoder.__class__.__name__)
                state = stDecodeValue
            if state == stDecodeValue:
                if not recursiveFlag and not substrateFun:  # legacy
                    def substrateFun(a, b, c):
                        return a, b[:c]
                if length == -1:  # indef length
                    value, substrate = concreteDecoder.indefLenValueDecoder(
                        fullSubstrate, substrate, asn1Spec, tagSet, length,
                        stGetValueDecoder, self, substrateFun
                    )
                else:
                    value, substrate = concreteDecoder.valueDecoder(
                        fullSubstrate, substrate, asn1Spec, tagSet, length,
                        stGetValueDecoder, self, substrateFun
                    )
                state = stStop
                debug.logger and debug.logger & debug.flagDecoder and debug.logger(
                    'codec %s yields type %s, value:\n%s\n...remaining substrate is: %s' % (concreteDecoder.__class__.__name__, value.__class__.__name__, value.prettyPrint(), substrate and debug.hexdump(substrate) or '<none>'))
            if state == stErrorCondition:
                raise error.PyAsn1Error(
                    '%s not in asn1Spec: %s' % (tagSet, asn1Spec)
                )
        if debug.logger and debug.logger & debug.flagDecoder:
            debug.scope.pop()
            debug.logger('decoder left scope %s, call completed' % debug.scope)
        return value, substrate


#: Turns BER octet stream into an ASN.1 object.
#:
#: Takes BER octetstream and decode it into an ASN.1 object
#: (e.g. :py:class:`~pyasn1.type.base.PyAsn1Item` derivative) which
#: may be a scalar or an arbitrary nested structure.
#:
#: Parameters
#: ----------
#: substrate: :py:class:`bytes` (Python 3) or :py:class:`str` (Python 2)
#:     BER octetstream
#:
#: asn1Spec: any pyasn1 type object e.g. :py:class:`~pyasn1.type.base.PyAsn1Item` derivative
#:     A pyasn1 type object to act as a template guiding the decoder. Depending on the ASN.1 structure
#:     being decoded, *asn1Spec* may or may not be required. Most common reason for
#:     it to require is that ASN.1 structure is encoded in *IMPLICIT* tagging mode.
#:
#: Returns
#: -------
#: : :py:class:`tuple`
#:     A tuple of pyasn1 object recovered from BER substrate (:py:class:`~pyasn1.type.base.PyAsn1Item` derivative)
#:     and the unprocessed trailing portion of the *substrate* (may be empty)
#:
#: Raises
#: ------
#: : :py:class:`pyasn1.error.PyAsn1Error`
#:     On decoding errors
decode = Decoder(tagMap, typeMap)

# XXX
# non-recursive decoding; return position rather than substrate
