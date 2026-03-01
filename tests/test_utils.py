import unittest
from uhttp import server as uhttp_server


class TestDecodePercentEncoding(unittest.TestCase):

    def test_decode_percent_encoding(self):
        res = uhttp_server.decode_percent_encoding(
            b'%7E%21%40%23%24%25%5E%26*%28%29_%2B%7B%7D%7C%3A%22%3C%3E%3F%60'
            b'-%3D%5B%5D%5C%3B%27%2C.%2F+')
        self.assertEqual(res, b'~!@#$%^&*()_+{}|:"<>?`-=[]\\;\',./ ')

    def test_percent_decode_encoding_utf8(self):
        res = uhttp_server.decode_percent_encoding(
            b'a%C3%A1%C3%A4bc%C4%8Dd%C4%8Fe%C3%A9%C4%9Bfghi%C3%ADjkl%C4%BAmn'
            b'%C5%88o%C3%B3%C3%B4pkqr%C5%95%C5%99s%C5%A1t%C5%A5u%C3%BA%C5%AF'
            b'vwxy%C3%BDz%C5%BE')
        self.assertEqual(
            res.decode('utf-8'),
            'aáäbcčdďeéěfghiíjklĺmnňoóôpkqrŕřsštťuúůvwxyýzž')

    def test_percent_decode_encoding_binary(self):
        res = uhttp_server.decode_percent_encoding(
            b'%00%01%02%03%04%05%06%07%08%09%0A%0B%0C%0D%0E%0F%10%11%12%13%14'
            b'%15%16%17%18%19%1A%1B%1C%1D%1E%1F%20%21%22%23%24%25%26%27%28%29'
            b'%2A%2B%2C-.%2F0123456789%3A%3B%3C%3D%3E%3F%40ABCDEFGHIJKLMNOPQR'
            b'STUVWXYZ%5B%5C%5D%5E_%60abcdefghijklmnopqrstuvwxyz%7B%7C%7D~%7F'
            b'%80%81%82%83%84%85%86%87%88%89%8A%8B%8C%8D%8E%8F%90%91%92%93%94'
            b'%95%96%97%98%99%9A%9B%9C%9D%9E%9F%A0%A1%A2%A3%A4%A5%A6%A7%A8%A9'
            b'%AA%AB%AC%AD%AE%AF%B0%B1%B2%B3%B4%B5%B6%B7%B8%B9%BA%BB%BC%BD%BE'
            b'%BF%C0%C1%C2%C3%C4%C5%C6%C7%C8%C9%CA%CB%CC%CD%CE%CF%D0%D1%D2%D3'
            b'%D4%D5%D6%D7%D8%D9%DA%DB%DC%DD%DE%DF%E0%E1%E2%E3%E4%E5%E6%E7%E8'
            b'%E9%EA%EB%EC%ED%EE%EF%F0%F1%F2%F3%F4%F5%F6%F7%F8%F9%FA%FB%FC%FD'
            b'%FE%FF')
        self.assertEqual(res, bytes(range(256)))


class TestSplitIter(unittest.TestCase):

    def test_basic_split(self):
        result = list(uhttp_server.split_iter('a;b;c', ';'))
        self.assertEqual(result, ['a', 'b', 'c'])

    def test_bytes_split(self):
        result = list(uhttp_server.split_iter(b'a&b&c', b'&'))
        self.assertEqual(result, [b'a', b'b', b'c'])

    def test_single_element(self):
        result = list(uhttp_server.split_iter('abc', ';'))
        self.assertEqual(result, ['abc'])

    def test_empty_string(self):
        result = list(uhttp_server.split_iter('', ';'))
        self.assertEqual(result, [''])

    def test_empty_parts(self):
        result = list(uhttp_server.split_iter('a;;b', ';'))
        self.assertEqual(result, ['a', '', 'b'])

    def test_trailing_separator(self):
        result = list(uhttp_server.split_iter('a;b;', ';'))
        self.assertEqual(result, ['a', 'b', ''])

    def test_leading_separator(self):
        result = list(uhttp_server.split_iter(';a;b', ';'))
        self.assertEqual(result, ['', 'a', 'b'])

    def test_multi_char_separator(self):
        result = list(uhttp_server.split_iter('a::b::c', '::'))
        self.assertEqual(result, ['a', 'b', 'c'])

    def test_generator_behavior(self):
        gen = uhttp_server.split_iter('a;b;c', ';')
        self.assertEqual(next(gen), 'a')
        self.assertEqual(next(gen), 'b')
        self.assertEqual(next(gen), 'c')
        with self.assertRaises(StopIteration):
            next(gen)

    def test_matches_split_str(self):
        test_cases = ['a;b;c', ';a;b', 'a;b;', ';;', 'abc', '']
        for case in test_cases:
            self.assertEqual(
                list(uhttp_server.split_iter(case, ';')),
                case.split(';'))

    def test_matches_split_bytes(self):
        test_cases = [b'a&b&c', b'&a&b', b'a&b&', b'&&', b'abc', b'']
        for case in test_cases:
            self.assertEqual(
                list(uhttp_server.split_iter(case, b'&')),
                case.split(b'&'))


class TestParseHeaderParameters(unittest.TestCase):

    def test_parse_header_parameters(self):
        res = uhttp_server.parse_header_parameters(
            'xyz=123;abcd=efgh; ijkl=mnop')
        self.assertEqual(res, {'xyz': '123', 'abcd': 'efgh', 'ijkl': 'mnop'})


class TestParseQuery(unittest.TestCase):

    def test_empty(self):
        query = uhttp_server.parse_query(b'')
        self.assertEqual(query, {})

    def test_key_only(self):
        query = uhttp_server.parse_query(b'aa')
        self.assertEqual(query, {'aa': None})

    def test_key_only_multiple(self):
        query = uhttp_server.parse_query(b'aa&bb')
        self.assertEqual(query, {'aa': None, 'bb': None})

    def test_key_only_multiple_same(self):
        query = uhttp_server.parse_query(b'aa&aa')
        self.assertEqual(query, {'aa': [None, None]})

    def test_param_str(self):
        query = uhttp_server.parse_query(b'cc=xyz')
        self.assertEqual(query, {'cc': 'xyz'})

    def test_param_multiple(self):
        query = uhttp_server.parse_query(b'cc=xyz&dd=pqr')
        self.assertEqual(query, {'cc': 'xyz', 'dd': 'pqr'})

    def test_param_multiple_same(self):
        query = uhttp_server.parse_query(b'cc=xyz&&cc=pqr')
        self.assertEqual(query, {'cc': ['xyz', 'pqr']})

    def test_key_only_and_key_value(self):
        query = uhttp_server.parse_query(b'aa&aa=xyz')
        self.assertEqual(query, {'aa': [None, 'xyz']})

    def test_key_value_and_key_only(self):
        query = uhttp_server.parse_query(b'aa=xyz&aa')
        self.assertEqual(query, {'aa': ['xyz', None]})

    def test_param_mixed2(self):
        query = uhttp_server.parse_query(b'aa&bb&bb&cc&cc=xyz&&cc=pqr&dd=zzz')
        self.assertEqual(query, {
            'aa': None,
            'bb': [None, None],
            'cc': [None, 'xyz', 'pqr'],
            'dd': 'zzz'})


class TestParseHeaderLine(unittest.TestCase):

    def test_root(self):
        line = uhttp_server.parse_header_line(b'Content-Length: 123')
        self.assertEqual(line, ('content-length', '123'))


if __name__ == '__main__':
    unittest.main()
