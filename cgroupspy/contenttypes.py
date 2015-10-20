

class BaseContentType(object):

    def __str__(self):
        raise NotImplementedError("Please implement this method in subclass")

    def __repr__(self):
        return "<{self.__class__.__name__}: {self}>".format(self=self)

    @classmethod
    def from_string(cls, value):
        raise NotImplementedError("This method should return an instance of the content type")


class DeviceAccess(BaseContentType):
    TYPE_ALL = "all"
    TYPE_CHAR = "c"
    TYPE_BLOCK = "b"

    ACCESS_UNSPEC = 0
    ACCESS_READ = 1
    ACCESS_WRITE = 2
    ACCESS_MKNOD = 4

    def __init__(self, dev_type=None, major=None, minor=None, access=None):
        self.dev_type = dev_type or self.TYPE_ALL

        # the default behaviour of device access cgroups if unspecified is as follows
        self.major = major or "*"
        self.minor = minor or "*"
        self.access = access or (self.ACCESS_READ | self.ACCESS_WRITE | self.ACCESS_MKNOD)

    def _check_access_bit(self, offset):
        mask = 1 << offset
        return self.access & mask

    @property
    def can_read(self):
        return self._check_access_bit(0) == self.ACCESS_READ

    @property
    def can_write(self):
        return self._check_access_bit(1) == self.ACCESS_WRITE

    @property
    def can_mknod(self):
        return self._check_access_bit(2) == self.ACCESS_MKNOD

    @property
    def access_string(self):
        accstr = ""
        if self.can_read:
            accstr += "r"
        if self.can_write:
            accstr += "w"
        if self.can_mknod:
            accstr += "m"
        return accstr

    def __str__(self):
        return "{self.dev_type} {self.major}:{self.minor} {self.access_string}".format(self=self)

    @classmethod
    def from_string(cls, value):
        dev_type, major_minor, access_string = value.split()
        major, minor = major_minor.split(":")
        major = int(major) if major != "*" else None
        minor == int(minor) if minor != "*" else None

        access_mode = 0
        for idx, char in enumerate("rwm"):
            if char in access_string:
                access_mode |= (1 << idx)
        return cls(dev_type, major, minor, access_mode)


class DeviceThrottle(BaseContentType):

    def __init__(self, limit, major=None, minor=None, ):
        self.limit = limit
        self.major = major or "*"
        self.minor = minor or "*"

    def __str__(self):
        return "{self.major}:{self.minor} {self.limit}".format(self=self)

    @classmethod
    def from_string(cls, value):
        if not value:
            return None

        try:
            major_minor, limit = value.split()
            major, minor = major_minor.split(":")
            return cls(int(limit), major, minor)
        except:
            raise RuntimeError("Value {} cannot be converted to a string that matches the pattern: "
                               "[device major]:[device minor] [throttle limit in bytes]".format(value))
