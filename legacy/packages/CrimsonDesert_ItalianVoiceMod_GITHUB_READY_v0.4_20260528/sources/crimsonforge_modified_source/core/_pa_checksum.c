/*
 * PaChecksum - Pearl Abyss Bob Jenkins Lookup3 variant.
 * C extension for Python. ~100x faster than pure Python.
 *
 * Build: python3 setup_checksum.py build_ext --inplace
 *
 * Stable ABI / abi3 build: targets the Python 3.11 stable ABI so a
 * single ``_pa_checksum.cp311-abi3-win_amd64.pyd`` loads on every
 * CPython 3.11+ — including Blender's bundled interpreter
 * (Blender 4.2 LTS = 3.11, Blender 5.0/5.1 = 3.13, future = 3.14+).
 * Without this, the cp<N>-win_amd64 naming locks the .pyd to one
 * specific Python and Blender silently falls back to the
 * ``_pa_checksum_python`` pure-Python implementation, which is the
 * 100x-slower path the docstring warns about.
 */

#define Py_LIMITED_API 0x030B0000

#include <Python.h>
#include <stdint.h>
#include <string.h>

#define MASK 0xFFFFFFFF
#define PA_MAGIC 0x2145E233U

static inline uint32_t rol(uint32_t x, int k) {
    return (x << k) | (x >> (32 - k));
}

static inline uint32_t ror(uint32_t x, int k) {
    return (x >> k) | (x << (32 - k));
}

static uint32_t pa_checksum_impl(const uint8_t *data, Py_ssize_t length) {
    if (length == 0) return 0;

    uint32_t a, b, c;
    a = b = c = (uint32_t)length - PA_MAGIC;

    Py_ssize_t offset = 0;
    Py_ssize_t remaining = length;

    while (remaining > 12) {
        uint32_t w0, w1, w2;
        memcpy(&w0, data + offset, 4);
        memcpy(&w1, data + offset + 4, 4);
        memcpy(&w2, data + offset + 8, 4);

        a += w0;
        b += w1;
        c += w2;

        a -= c; a ^= rol(c, 4);  c += b;
        b -= a; b ^= rol(a, 6);  a += c;
        c -= b; c ^= rol(b, 8);  b += a;
        a -= c; a ^= rol(c, 16); c += b;
        b -= a; b ^= rol(a, 19); a += c;
        c -= b; c ^= rol(b, 4);  b += a;

        offset += 12;
        remaining -= 12;
    }

    /* Handle remaining bytes (fall-through from 12 down to 1) */
    if (remaining >= 12) c += ((uint32_t)data[offset + 11]) << 24;
    if (remaining >= 11) c += ((uint32_t)data[offset + 10]) << 16;
    if (remaining >= 10) c += ((uint32_t)data[offset + 9]) << 8;
    if (remaining >= 9)  c += (uint32_t)data[offset + 8];
    if (remaining >= 8)  b += ((uint32_t)data[offset + 7]) << 24;
    if (remaining >= 7)  b += ((uint32_t)data[offset + 6]) << 16;
    if (remaining >= 6)  b += ((uint32_t)data[offset + 5]) << 8;
    if (remaining >= 5)  b += (uint32_t)data[offset + 4];
    if (remaining >= 4)  a += ((uint32_t)data[offset + 3]) << 24;
    if (remaining >= 3)  a += ((uint32_t)data[offset + 2]) << 16;
    if (remaining >= 2)  a += ((uint32_t)data[offset + 1]) << 8;
    if (remaining >= 1)  a += (uint32_t)data[offset];

    /* Finalization with both ROL and ROR */
    uint32_t v82 = (b ^ c) - rol(b, 14);
    uint32_t v83 = (a ^ v82) - rol(v82, 11);
    uint32_t v84 = (v83 ^ b) - ror(v83, 7);
    uint32_t v85 = (v84 ^ v82) - rol(v84, 16);
    uint32_t v86 = rol(v85, 4);
    uint32_t t = (v83 ^ v85) - v86;
    uint32_t v87 = (t ^ v84) - rol(t, 14);

    return (v87 ^ v85) - ror(v87, 8);
}

static PyObject* py_pa_checksum(PyObject *self, PyObject *args) {
    Py_buffer buf;
    if (!PyArg_ParseTuple(args, "y*", &buf))
        return NULL;

    uint32_t result = pa_checksum_impl((const uint8_t *)buf.buf, buf.len);
    PyBuffer_Release(&buf);

    return PyLong_FromUnsignedLong(result);
}

static PyObject* py_checksum_file(PyObject *self, PyObject *args) {
    const char *path;
    int skip_header = 0;
    if (!PyArg_ParseTuple(args, "s|i", &path, &skip_header))
        return NULL;

    FILE *f = fopen(path, "rb");
    if (!f) {
        PyErr_Format(PyExc_FileNotFoundError, "Cannot open file: %s", path);
        return NULL;
    }

    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);

    if (skip_header >= file_size) {
        fclose(f);
        return PyLong_FromUnsignedLong(0);
    }

    fseek(f, skip_header, SEEK_SET);
    Py_ssize_t data_size = file_size - skip_header;

    uint8_t *data = (uint8_t *)malloc(data_size);
    if (!data) {
        fclose(f);
        PyErr_NoMemory();
        return NULL;
    }

    Py_ssize_t read = fread(data, 1, data_size, f);
    fclose(f);

    if (read != data_size) {
        free(data);
        PyErr_Format(PyExc_IOError, "Short read: expected %zd, got %zd", data_size, read);
        return NULL;
    }

    uint32_t result = pa_checksum_impl(data, data_size);
    free(data);

    return PyLong_FromUnsignedLong(result);
}

static PyMethodDef methods[] = {
    {"pa_checksum", py_pa_checksum, METH_VARARGS,
     "Compute PaChecksum (Pearl Abyss Bob Jenkins Lookup3 variant) on bytes."},
    {"checksum_file", py_checksum_file, METH_VARARGS,
     "Compute PaChecksum on a file, optionally skipping header bytes."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_pa_checksum",
    "Fast C implementation of PaChecksum for Crimson Desert PAZ/PAMT/PAPGT files.",
    -1,
    methods
};

PyMODINIT_FUNC PyInit__pa_checksum(void) {
    return PyModule_Create(&module);
}
