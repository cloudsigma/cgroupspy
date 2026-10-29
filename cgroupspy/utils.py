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
DISCLAIMED. IN NO EVENT SHALL CLOUDSIGMA AG BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import os
import sys

text_type = type(u'')


def to_bytes(value):
    """Encode filesystem names without changing the public bytes path API."""
    if isinstance(value, bytes):
        return value
    if not isinstance(value, text_type) and hasattr(os, 'fspath'):
        value = os.fspath(value)
        if isinstance(value, bytes):
            return value
    if not isinstance(value, text_type):
        raise TypeError('A filesystem path must be text or bytes')
    if hasattr(os, 'fsencode'):
        return os.fsencode(value)
    return value.encode(sys.getfilesystemencoding() or 'utf-8')


def to_text(value):
    if isinstance(value, text_type):
        return value
    if hasattr(os, 'fsdecode'):
        return os.fsdecode(value)
    return value.decode(sys.getfilesystemencoding() or 'utf-8')


def validate_child_name(name):
    """Return a bytes name restricted to one child of the selected directory."""
    name = to_bytes(name)
    if (not name or name in (b'.', b'..') or b'\x00' in name or
            b'/' in name or (os.altsep and to_bytes(os.altsep) in name)):
        raise ValueError('A cgroup name must be a single nonempty path component')
    return name


def normalize_tree_path(path):
    path = to_bytes(path)
    if path:
        return path.rstrip(b'/') or b'/'
    return path


def is_safe_controller_alias(path, root):
    """Recognize a v1 controller alias confined to an immediate sibling mount."""
    return (os.path.islink(path) and os.path.isdir(path) and
            os.path.dirname(os.path.realpath(path)) == os.path.realpath(root))


def walk_tree(root):
    """Pre-order depth-first"""
    yield root

    for child in root.children:
        for el in walk_tree(child):
            yield el


def walk_up_tree(root):
    """Post-order depth-first"""
    for child in root.children:
        for el in walk_up_tree(child):
            yield el

    yield root


def split_path_components(path):
    return [component for component in to_text(path).split('/') if component]
