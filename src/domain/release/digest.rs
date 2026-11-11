//! Canonical digests and strict JSON.

use std::fmt;

use serde::de::{DeserializeSeed, Deserializer, Error as _, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use sha1::Sha1;
use sha2::{Digest, Sha256};

use super::require;
use crate::domain::work::ports::{PortError, PortResult};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// SHA-256 of the canonical JSON of `value`: sorted keys, no spaces,
/// non-ASCII text as it is. The old Python tool made the same bytes.
pub fn digest(value: &Value) -> String {
    hex(&Sha256::digest(canonical(value).as_bytes()))
}

fn canonical(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(canonical).collect();
            format!("[{}]", parts.join(","))
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let parts: Vec<String> = keys
                .into_iter()
                .map(|k| format!("{}:{}", Value::String(k.clone()), canonical(&map[k])))
                .collect();
            format!("{{{}}}", parts.join(","))
        }
        scalar => scalar.to_string(),
    }
}

/// The git blob id of `raw`.
pub fn blob_sha(raw: &[u8]) -> String {
    let mut hasher = Sha1::new();
    hasher.update(format!("blob {}\0", raw.len()).as_bytes());
    hasher.update(raw);
    hex(&hasher.finalize())
}

/// 40 lowercase hexadecimal characters: one exact commit or blob.
pub fn is_sha(text: &str) -> bool {
    text.len() == 40 && is_hex(text)
}

/// 64 lowercase hexadecimal characters: one SHA-256.
pub fn is_sha256(text: &str) -> bool {
    text.len() == 64 && is_hex(text)
}

fn is_hex(text: &str) -> bool {
    text.bytes()
        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Decode standard base64 text; white space is ignored. `None` when the
/// text is not valid base64.
pub fn decode_base64(text: &str) -> Option<Vec<u8>> {
    use base64::Engine as _;
    let compact: String = text.split_whitespace().collect();
    base64::engine::general_purpose::STANDARD
        .decode(compact)
        .ok()
}

/// Parse JSON. A duplicate key in an object is an error.
pub fn strict_json(text: &str) -> PortResult<Value> {
    let mut de = serde_json::Deserializer::from_str(text);
    let value = Strict
        .deserialize(&mut de)
        .map_err(|e| PortError(format!("release: invalid JSON: {e}")))?;
    require(de.end().is_ok(), "invalid JSON: trailing text")?;
    Ok(value)
}

struct Strict;

impl<'de> DeserializeSeed<'de> for Strict {
    type Value = Value;

    fn deserialize<D: Deserializer<'de>>(self, de: D) -> Result<Value, D::Error> {
        de.deserialize_any(Strict)
    }
}

impl<'de> Visitor<'de> for Strict {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON value")
    }

    fn visit_bool<E>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }

    fn visit_i64<E>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }

    fn visit_u64<E>(self, v: u64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }

    fn visit_f64<E>(self, v: f64) -> Result<Value, E> {
        Ok(Number::from_f64(v).map_or(Value::Null, Value::Number))
    }

    fn visit_str<E>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_string()))
    }

    fn visit_unit<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut items = Vec::new();
        while let Some(item) = seq.next_element_seed(Strict)? {
            items.push(item);
        }
        Ok(Value::Array(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
        let mut map = Map::new();
        while let Some(key) = access.next_key::<String>()? {
            if map.contains_key(&key) {
                return Err(A::Error::custom(format!("duplicate JSON key `{key}`")));
            }
            map.insert(key, access.next_value_seed(Strict)?);
        }
        Ok(Value::Object(map))
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn digest_is_the_python_canonical_form() {
        // python3: hashlib.sha256(json.dumps({"b":[1,"é"],"a":None},
        //   sort_keys=True, separators=(',',':'), ensure_ascii=False).encode())
        assert_eq!(
            canonical(&json!({"b": [1, "é"], "a": null})),
            r#"{"a":null,"b":[1,"é"]}"#
        );
        assert_eq!(
            digest(&json!({})),
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
        );
    }

    #[test]
    fn blob_sha_is_the_git_blob_id() {
        // `printf 'hello\n' | git hash-object --stdin`
        assert_eq!(
            blob_sha(b"hello\n"),
            "ce013625030ba8dba906f756967f9e9ca394464a"
        );
    }

    #[test]
    fn strict_json_rejects_duplicates_and_trailing_text() {
        let value = strict_json(r#"{"a": [1, -2, 1.5, true, null, "x"], "b": {"c": 1}}"#).unwrap();
        assert_eq!(
            value,
            json!({"a": [1, -2, 1.5, true, null, "x"], "b": {"c": 1}})
        );
        assert!(
            strict_json(r#"{"a": 1, "a": 2}"#)
                .unwrap_err()
                .0
                .contains("duplicate JSON key")
        );
        assert!(strict_json(r#"{"a": {"b": 1, "b": 1}}"#).is_err());
        assert!(strict_json("{} x").is_err());
        assert!(strict_json("{").is_err());
    }

    #[test]
    fn base64_ignores_white_space_only() {
        assert_eq!(decode_base64("aGVs\nbG8K\n").unwrap(), b"hello\n");
        assert!(decode_base64("aGVs*G8K").is_none());
        assert!(decode_base64("aGVsbG8").is_none());
    }

    #[test]
    fn sha_shapes() {
        assert!(is_sha(&"a1".repeat(20)));
        assert!(!is_sha(&"A1".repeat(20)));
        assert!(!is_sha("abc"));
        assert!(is_sha256(&"0f".repeat(32)));
        assert!(!is_sha256(&"0g".repeat(32)));
    }
}
