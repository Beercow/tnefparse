"""extracts TNEF encoded content from for example winmail.dat attachments.
"""
import logging
import os
from datetime import datetime
from uuid import UUID

from . import properties as Attribute
from .codepage import Codepage
from .mapi import decode_mapi
from .util import typtime, bytes_to_int, checksum, uint32, uint16, uint8

logger = logging.getLogger("tnef-decode")


class TNEFObject(object):
    "a TNEF object that may contain a property and an attachment"
    PTYPE_CLASS  = 0x1
    PTYPE_TIME   = 0x3
    PTYPE_STRING = 0x7

    def __init__(self, data, do_checksum=False):
        self.length = len(data)
        self.level = uint8(data, 0)
        self.name = uint16(data, 1)
        self.type = uint16(data, 3)
        self.length = min(self.length, uint32(data, 5) + 11)
        self.data = data[9 : self.length - 2]
        att_checksum = uint16(data, self.length - 2)

        if do_checksum:
            calc_checksum = checksum(self.data)
            if calc_checksum != att_checksum:
                logger.warning("Checksum: %s != %s" % (calc_checksum, att_checksum))
        else:
            calc_checksum = att_checksum

        # whether the checksum is ok
        self.good_checksum = calc_checksum == att_checksum

    @property
    def name_str(self):
        return TNEF.codes.get(self.name)

    def __str__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.name_str)


class TNEFAttachment(object):
    "a TNEF attachment"

    SZMAPI_UNSPECIFIED = 0x0000  # MAPI Unspecified
    SZMAPI_NULL = 0x0001  # MAPI null property
    SZMAPI_SHORT = 0x0002  # MAPI short (signed 16 bits)
    SZMAPI_INT = 0x0003  # MAPI integer (signed 32 bits)
    SZMAPI_FLOAT = 0x0004  # MAPI float (4 bytes)
    SZMAPI_DOUBLE = 0x0005  # MAPI double
    SZMAPI_CURRENCY = 0x0006  # MAPI currency (64 bits)
    SZMAPI_APPTIME = 0x0007  # MAPI application time
    SZMAPI_ERROR = 0x000A  # MAPI error (32 bits)
    SZMAPI_BOOLEAN = 0x000B  # MAPI boolean (16 bits)
    SZMAPI_OBJECT = 0x000D  # MAPI embedded object
    SZMAPI_INT8BYTE = 0x0014  # MAPI 8 byte signed int
    SZMAPI_STRING = 0x001E  # MAPI string
    SZMAPI_UNICODE_STRING = 0x001F  # MAPI unicode-string (null terminated)
    SZMAPI_SYSTIME = 0x0040  # MAPI time (64 bits)
    SZMAPI_CLSID = 0x0048  # MAPI OLE GUID
    SZMAPI_BINARY = 0x0102  # MAPI binary
    SZMAPI_BEATS_THE_HELL_OUTTA_ME = 0x0033

    codes = {
        SZMAPI_UNSPECIFIED: "MAPI Unspecified",
        SZMAPI_NULL: "MAPI null property",
        SZMAPI_SHORT: "MAPI short (signed 16 bits)",
        SZMAPI_INT: "MAPI integer (signed 32 bits)",
        SZMAPI_FLOAT: "MAPI float (4 bytes)",
        SZMAPI_DOUBLE: "MAPI double",
        SZMAPI_CURRENCY: "MAPI currency (64 bits)",
        SZMAPI_APPTIME: "MAPI application time",
        SZMAPI_ERROR: "MAPI error (32 bits)",
        SZMAPI_BOOLEAN: "MAPI boolean (16 bits)",
        SZMAPI_OBJECT: "MAPI embedded object",
        SZMAPI_INT8BYTE: "MAPI 8 byte signed int",
        SZMAPI_STRING: "MAPI string",
        SZMAPI_UNICODE_STRING: "MAPI unicode-string (null terminated)",
        # SZMAPI_PT_SYSTIME              :  "MAPI time (after 2038/01/17 22:14:07 or before 1970/01/01 00:00:00)",
        SZMAPI_SYSTIME: "MAPI time (64 bits)",
        SZMAPI_CLSID: "MAPI OLE GUID",
        SZMAPI_BINARY: "MAPI binary",
        SZMAPI_BEATS_THE_HELL_OUTTA_ME: "Unknown",
    }

    def __init__(self, codepage):
        self.codepage = codepage
        self.mapi_attrs = []
        self._name = b''
        self.data = b''

    @property
    def name(self):
        if isinstance(self._name, bytes):
            return self._name.decode().strip('\x00')
        else:
            return self._name.strip('\x00')

    def long_filename(self):
        atname = Attribute.MAPI_ATTACH_LONG_FILENAME
        name = [a.data for a in self.mapi_attrs if a.name == atname]
        if name:
            return name[0]
        return self.name

    def add_attr(self, attribute):
        # For now, we ignore rendering/preview properties
        if attribute.name == TNEF.ATTATTACHMODIFYDATE:
            self.modification_date = typtime(attribute.data)
        elif attribute.name == TNEF.ATTATTACHCREATEDATE:
            self.creation_date = typtime(attribute.data)
        elif attribute.name == TNEF.ATTATTACHMENT:
            mapi_attrs = decode_mapi(attribute.data, self.codepage)
            for p in mapi_attrs:
                if p.name == Attribute.MAPI_ATTACH_FILENAME:
                    self._name = p.data
                elif p.name == Attribute.MAPI_ATTACH_DATA_OBJ:
                    self.data = p.data
                elif p.name == Attribute.MAPI_ATTACH_RENDERING:
                    pass
                else:
                    self.mapi_attrs.append(p)
        elif attribute.name == TNEF.ATTATTACHTITLE:
            self._name = attribute.data
        elif attribute.name == TNEF.ATTATTACHDATA:
            self.data = attribute.data
        elif attribute.name == TNEF.ATTATTACHRENDDATA:
            pass
        elif attribute.name == TNEF.ATTATTACHMETAFILE:
            pass
            # this is a WMF file of some kind
        else:
            logger.debug("Unknown attribute name: %s" % attribute)

    def __str__(self):
        return "<ATTCH:'%s'>" % self.long_filename()


class TNEF(object):
    "main decoder class - start by using this"

    TNEF_SIGNATURE = 0x223E9F78
    LVL_MESSAGE = 0x01
    LVL_ATTACHMENT = 0x02
    VALID_VERSION = 0x10000

    ATTOWNER = 0x0000  # Owner
    ATTSENTFOR = 0x0001  # Sent For
    ATTDELEGATE = 0x0002  # Delegate
    ATTDATESTART = 0x0006  # Date Start
    ATTDATEEND = 0x0007  # Date End
    ATTAIDOWNER = 0x0008  # Owner Appointment ID
    ATTREQUESTRES = 0x0009  # Response Requested.
    ATTFROM = 0x8000  # From
    ATTSUBJECT = 0x8004  # Subject
    ATTDATESENT = 0x8005  # Date Sent
    ATTDATERECD = 0x8006  # Date Recieved
    ATTMESSAGESTATUS = 0x8007  # Message Status
    ATTMESSAGECLASS = 0x8008  # Message Class
    ATTMESSAGEID = 0x8009  # Message ID
    ATTPARENTID = 0x800A  # Parent ID
    ATTCONVERSATIONID = 0x800B  # Conversation ID
    ATTBODY = 0x800C  # Body
    ATTPRIORITY = 0x800D  # Priority
    ATTATTACHDATA = 0x800F  # Attachment Data
    ATTATTACHTITLE = 0x8010  # Attachment File Name
    ATTATTACHMETAFILE = 0x8011  # Attachment Meta File
    ATTATTACHCREATEDATE = 0x8012  # Attachment Creation Date
    ATTATTACHMODIFYDATE = 0x8013  # Attachment Modification Date
    ATTDATEMODIFY = 0x8020  # Date Modified
    ATTATTACHTRANSPORTFILENAME = 0x9001  # Attachment Transport Filename
    ATTATTACHRENDDATA = 0x9002  # Attachment Rendering Data
    ATTMAPIPROPS = 0x9003  # MAPI Properties
    ATTRECIPTABLE = 0x9004  # Recipients
    ATTATTACHMENT = 0x9005  # Attachment
    ATTTNEFVERSION = 0x9006  # TNEF Version
    ATTOEMCODEPAGE = 0x9007  # OEM Codepage
    ATTORIGNINALMESSAGECLASS = 0x9008  # Original Message Class

    codes = {
        ATTOWNER: "Owner",
        ATTSENTFOR: "Sent For",
        ATTDELEGATE: "Delegate",
        ATTDATESTART: "Date Start",
        ATTDATEEND: "Date End",
        ATTAIDOWNER: "Owner Appointment ID",
        ATTREQUESTRES: "Response Requested",
        ATTFROM: "From",
        ATTSUBJECT: "Subject",
        ATTDATESENT: "Date Sent",
        ATTDATERECD: "Date Received",
        ATTMESSAGESTATUS: "Message Status",
        ATTMESSAGECLASS: "Message Class",
        ATTMESSAGEID: "Message ID",
        ATTPARENTID: "Parent ID",
        ATTCONVERSATIONID: "Conversation ID",
        ATTBODY: "Body",
        ATTPRIORITY: "Priority",
        ATTATTACHDATA: "Attachment Data",
        ATTATTACHTITLE: "Attachment File Name",
        ATTATTACHMETAFILE: "Attachment Meta File",
        ATTATTACHCREATEDATE: "Attachment Creation Date",
        ATTATTACHMODIFYDATE: "Attachment Modification Date",
        ATTDATEMODIFY: "Date Modified",
        ATTATTACHTRANSPORTFILENAME: "Attachment Transport Filename",
        ATTATTACHRENDDATA: "Attachment Rendering Data",
        ATTMAPIPROPS: "MAPI Properties",
        ATTRECIPTABLE: "Recipients",
        ATTATTACHMENT: "Attachment",
        ATTTNEFVERSION: "TNEF Version",
        ATTOEMCODEPAGE: "OEM Codepage",
        ATTORIGNINALMESSAGECLASS: "Original Message Class",
    }

    MIN_OBJ_SIZE = 12

    def __init__(self, data, do_checksum=True):
        self.signature = uint32(data)
        if self.signature != TNEF.TNEF_SIGNATURE:
            raise ValueError("Wrong TNEF signature: 0x%2.8x" % self.signature)
        self.key = uint16(data, 4)
        self.codepage = None
        self.objects = []
        self.attachments = []
        self.mapiprops = []
        self.msgprops = []
        self.body = None
        self.htmlbody = None
        self._rtfbody = None
        offset = 6

        if not do_checksum:
            logger.info("Skipping checksum for performance")

        while offset + self.MIN_OBJ_SIZE < len(data):
            obj = TNEFObject(data[offset:], do_checksum)
            offset += obj.length
            self.objects.append(obj)

            # handle attachments
            if obj.name == TNEF.ATTATTACHRENDDATA:
                attachment = TNEFAttachment(self.codepage)
                self.attachments.append(attachment)

            # print(TNEF.codes.get(obj.name), hex(obj.type))

            if obj.level == TNEF.LVL_ATTACHMENT:
                attachment.add_attr(obj)
            elif obj.name == TNEF.ATTMAPIPROPS:
                # handle MAPI properties
                mapiprops = decode_mapi(obj.data, self.codepage)
                internet_codepage = None

                # handle BODY property
                for p in mapiprops:
                    if p.name == Attribute.MAPI_INTERNET_CODEPAGE:
                        internet_codepage = Codepage(p.data)
                    if p.name == Attribute.MAPI_BODY:
                        self.body = p.data
                    elif p.name == Attribute.MAPI_UNCOMPRESSED_BODY:
                        self.body = p.data
                    elif p.name == Attribute.MAPI_BODY_HTML:
                        self.htmlbody = p.data
                    elif p.name == Attribute.MAPI_RTF_COMPRESSED:
                        self._rtfbody = p.data
                    else:
                        self.mapiprops.append(p)
                if self.htmlbody and internet_codepage:
                    self.htmlbody = internet_codepage.decode(self.htmlbody)
                if self.body and internet_codepage:
                    self.body = internet_codepage.decode(self.body)
            elif obj.name == TNEF.ATTBODY:
                self.body = obj.data
            elif obj.name == TNEF.ATTTNEFVERSION:
                if uint32(obj.data) != TNEF.VALID_VERSION:
                    logger.warning('Invalid TNEF Version %02x%02x%02x%02x', *obj.data)
            elif obj.name == TNEF.ATTOEMCODEPAGE:
                self.codepage = Codepage(uint32(obj.data)).codepage()
            elif obj.type in (TNEFObject.PTYPE_CLASS, TNEFObject.PTYPE_STRING):
                obj.data = obj.data.decode(self.codepage).rstrip('\x00')
                self.msgprops.append(obj)
            elif obj.name == TNEF.ATTPRIORITY:
                obj.data = 3 - uint16(obj.data)
                self.msgprops.append(obj)
            elif obj.name == TNEF.ATTRECIPTABLE:
                rows = uint32(obj.data)
                att_offset = 4
                recipients = []
                for _ in range(rows):
                    att_offset, recipient = decode_mapi(obj.data, self.codepage, starting_offset=att_offset)
                    recipients.append(recipient)
                obj.data = recipients
                self.msgprops.append(obj)
            elif obj.name == TNEF.ATTFROM:
                obj.data = triples(obj.data)
                self.msgprops.append(obj)
            elif obj.name == TNEF.ATTREQUESTRES:
                obj.data = bool(uint16(obj.data))
                self.msgprops.append(obj)
            elif obj.name == TNEF.ATTMESSAGESTATUS:
                # documented to be a uint32, observed to be a single byte
                obj.data = bytes_to_int(obj.data)
                self.msgprops.append(obj)
            elif obj.type == TNEFObject.PTYPE_TIME and obj.name in (
                TNEF.ATTDATESTART, TNEF.ATTDATEMODIFY, TNEF.ATTDATESENT, TNEF.ATTDATERECD
            ):
                try:
                    obj.data = typtime(obj.data)
                    self.msgprops.append(obj)
                except ValueError:
                    logger.debug("TNEF Object not a valid date: %s" % obj)
            else:
                logger.debug("Unhandled TNEF Object: %s" % obj)

    def has_body(self):
        return True if (self.body or self.htmlbody or self._rtfbody) else False

    @property
    def rtfbody(self):
        if self._rtfbody:
            try:
                from compressed_rtf import decompress
                return decompress(self._rtfbody + b'\x00')
            except ImportError:
                logger.warning("Returning compressed RTF. Install compressed_rtf to decompress")
                return self._rtfbody
        else:
            return None

    def __str__(self):
        atts = (", %i attachments" % len(self.attachments)) if self.attachments else ''
        return "<%s:0x%2.2x%s>" % (self.__class__.__name__, self.key, atts)

    def dump(self, force_strings=False):
        def get_data(a):
            if force_strings and isinstance(a.data, bytes):
                return a.data.decode('ascii', errors="replace")
            elif force_strings and isinstance(a.data, tuple) and isinstance(a.data[0], bytes):
                return [s.decode('ascii', errors="replace") for s in a.data]
            elif force_strings and (isinstance(a.data, datetime) or isinstance(a.data, UUID)):
                return a.data.__str__()
            else:
                return a.data
        out = {'attachments': [], 'attributes': {}, 'extended_attributes': {}}
        for o in self.attachments:
            attachment = {
                'filename': o.name,
                'long_filename': o.long_filename(),
                'data_len': len(o.data),
            }
            for att in o.mapi_attrs:
                attachment[att.name_str] = get_data(att)
            out['attachments'].append(attachment)
        for o in self.msgprops:
            data = get_data(o)
            if o.name == TNEF.ATTRECIPTABLE:
                data = []
                for recipient in o.data:
                    rec = {}
                    for att in recipient:
                        rec[att.name_str] = get_data(att)
                    data.append(rec)
            out['attributes'][o.name_str] = data
        for att in self.mapiprops:
            out['extended_attributes'][att.name_str] = get_data(att)
        return out


def valid_version(data):
    version = uint32(data)
    return version == 0x10000


def triples(data):
    assert uint16(data) == 4
    # struct_length = uint16(data, 2)
    sender_length = uint16(data, 4)
    email_length = uint16(data, 6)
    sender = data[8 : 8 + sender_length]
    etype_email = data[8 + sender_length : 8 + sender_length + email_length]
    etype, email = etype_email.split(b':', 1)

    return sender.rstrip(b'\x00'), etype, email.rstrip(b'\x00')


def to_zip(data, default_name=u'no-name', deflate=True):
    "Convert attachments in TNEF data to zip format. Accepts and returns str type."
    # Parse the TNEF data
    tnef = TNEF(data)

    # Convert the TNEF file to an equivalent ZIP file
    tozip = {}
    for attachment in tnef.attachments:
        filename = attachment.name or default_name
        L = len(tozip.get(filename, []))
        if L > 0:
            # uniqify this file name by adding -<num> before the extension
            root, ext = os.path.splitext(filename)
            tozip[filename].append((attachment.data, str("%s-%d%s" % (root, L + 1, ext))))
        else:
            tozip[filename] = [(attachment.data, filename)]

    # Add each attachment in the TNEF file to the zip file
    from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED
    from io import BytesIO
    import contextlib

    sfp = BytesIO()
    zf = ZipFile(sfp, "w", ZIP_DEFLATED if deflate else ZIP_STORED)
    with contextlib.closing(zf) as z:
        for filename, entries in list(tozip.items()):
            for entry in entries:
                data, name = entry
                z.writestr(name, data)

    # Return the binary data for the zip file
    return sfp.getvalue()
