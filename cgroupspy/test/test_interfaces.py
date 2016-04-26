"""
Copyright (c) 2014, CloudSigma AG
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the CloudSigma AG nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL CloudSigma AG BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
from unittest import TestCase

import mock

from ..interfaces import BaseFileInterface, FlagFile, BitFieldFile, CommaDashSetFile, DictFile, \
    IntegerFile, IntegerListFile, ListFile, MultiLineIntegerFile


class FaceHolder(object):
    face = None

    def __init__(self, init_value):
        self.val = init_value
        self.last_filename = None

    def get_property(self, filename):
        self.last_filename = filename
        return self.val

    def set_property(self, filename, val):
        self.last_filename = filename
        self.val = str(val)


class InterfacesTest(TestCase):

    def patch_face(self, **kwargs):
        patch = mock.patch.multiple(FaceHolder, **kwargs)
        patch.start()
        self.addCleanup(patch.stop)

    def test_base(self):
        self.patch_face(face=BaseFileInterface("myfile1"))
        fh = FaceHolder("23")
        self.assertEqual(fh.face, "23")
        fh.face = 44
        self.assertEqual(fh.face, "44")

    def test_flagfile(self):
        self.patch_face(face=FlagFile("flagfile"))
        fh = FaceHolder("1")
        self.assertEqual(fh.face, True)

        fh.face = False
        self.assertEqual(fh.face, False)
        self.assertEqual(fh.val, "0")

        fh.face = None
        self.assertEqual(fh.face, False)
        self.assertEqual(fh.val, "0")

        fh.face = 1
        self.assertEqual(fh.face, True)
        self.assertEqual(fh.val, "1")

    def test_bitfieldfile(self):
        self.patch_face(face=BitFieldFile("bitfieldfile"))
        fh = FaceHolder("2")
        self.assertEqual(fh.face, [False, True, False, False, False, False, False, False])

        fh.face = [False]
        self.assertEqual(fh.face, [False, False, False, False, False, False, False, False])
        self.assertEqual(fh.val, "0")

        fh.face = [False, True, True]
        self.assertEqual(fh.face, [False, True, True, False, False, False, False, False])
        self.assertEqual(fh.val, "6")

    def test_comma_dash(self):
        self.patch_face(face=CommaDashSetFile("commadash"))
        fh = FaceHolder("1,2,4-6,18-23,7")

        expected = {1, 2, 4, 5, 6, 7, 18, 19, 20, 21, 22, 23}
        self.assertEqual(fh.face, expected)

        fh.face = {1, 2, 3}
        self.assertEqual(fh.face, {1, 2, 3})
        self.assertEqual(fh.val, "1,2,3")

        fh.face = {1}
        self.assertEqual(fh.face, {1})
        self.assertEqual(fh.val, "1")

        fh.face = {}
        self.assertEqual(fh.face, set([]))
        self.assertEqual(fh.val, " ")

    def test_dict_file(self):
        self.patch_face(face=DictFile("dictfile"))
        fh = FaceHolder("ala 123\nbala 123\nnica 456")
        self.assertEqual(fh.face, {"ala": 123, "bala": 123, "nica": 456})

    def test_int_file(self):
        self.patch_face(face=IntegerFile("intfile"))
        fh = FaceHolder("16")
        self.assertEqual(fh.face, 16)

        fh.face = 18
        self.assertEqual(fh.face, 18)
        self.assertEqual(fh.val, "18")

        fh.face = None
        self.assertEqual(fh.face, None)
        self.assertEqual(fh.val, "-1")

    def test_int_list(self):
        self.patch_face(face=IntegerListFile("intlistfile"))

        fh = FaceHolder("16 18 20")
        self.assertEqual(fh.face, [16, 18, 20])

    def test_list(self):
        self.patch_face(face=ListFile("listfile"))

        fh = FaceHolder("16 18 20")
        self.assertEqual(fh.face, ["16", "18", "20"])

    def test_multiline_int(self):
        self.patch_face(face=MultiLineIntegerFile("multiint"))
        fh = FaceHolder("16\n18\n20\n22")
        self.assertEqual(fh.face, [16, 18, 20, 22])
