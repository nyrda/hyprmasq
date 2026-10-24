package server

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"strconv"
	"strings"
)

// structuredJSON encodes a result's structured content for the SDK, which sends encoded content as it is. JSON has no
// NaN or infinity, and the SDK sends no reply at all for a result that does not encode, so the client would wait for
// one until it gives up. Content that holds such a number is encoded from a copy with null in its place, by the rules
// of encoding/json for the struct tags that the results use.
func structuredJSON(v any) (json.RawMessage, error) {
	data, err := json.Marshal(v)
	var unsupported *json.UnsupportedValueError
	if err == nil || !errors.As(err, &unsupported) {
		return data, err
	}
	return json.Marshal(finite(reflect.ValueOf(v)))
}

// marshalerOf is the json.Marshaler that encoding/json calls for the value, or nil: the value's own, also a pointer's
// that is not nil, or that of its address when it has one.
func marshalerOf(v reflect.Value) json.Marshaler {
	marshaler := reflect.TypeFor[json.Marshaler]()
	switch {
	case v.Kind() == reflect.Interface || v.Kind() == reflect.Pointer && v.IsNil():
		return nil
	case v.Type().Implements(marshaler):
		return v.Interface().(json.Marshaler)
	case v.CanAddr() && reflect.PointerTo(v.Type()).Implements(marshaler):
		return v.Addr().Interface().(json.Marshaler)
	}
	return nil
}

// finite is the value as encoding/json would encode it, as maps, slices and plain values, with null for numbers that
// JSON cannot hold.
func finite(v reflect.Value) any {
	if !v.IsValid() {
		return nil
	}
	if m := marshalerOf(v); m != nil {
		data, err := m.MarshalJSON()
		if err != nil {
			return nil
		}
		return json.RawMessage(data)
	}
	switch v.Kind() {
	case reflect.Pointer, reflect.Interface:
		if v.IsNil() {
			return nil
		}
		return finite(v.Elem())
	case reflect.Float32, reflect.Float64:
		if f := v.Float(); math.IsNaN(f) || math.IsInf(f, 0) {
			return nil
		}
		return v.Interface()
	case reflect.Struct:
		out := map[string]any{}
		finiteFields(v, out)
		return out
	case reflect.Map:
		if v.IsNil() {
			return nil
		}
		out := make(map[string]any, v.Len())
		for it := v.MapRange(); it.Next(); {
			out[mapKey(it.Key())] = finite(it.Value())
		}
		return out
	case reflect.Slice:
		if v.IsNil() {
			return nil
		}
		if v.Type().Elem().Kind() == reflect.Uint8 {
			return v.Interface()
		}
		fallthrough
	case reflect.Array:
		out := make([]any, v.Len())
		for i := range out {
			out[i] = finite(v.Index(i))
		}
		return out
	}
	return v.Interface()
}

// finiteFields adds the fields of a struct to out as encoding/json names them, with the fields of embedded structs
// without a name of their own promoted.
func finiteFields(v reflect.Value, out map[string]any) {
	t := v.Type()
	for i := range t.NumField() {
		field := t.Field(i)
		name, options, _ := strings.Cut(field.Tag.Get("json"), ",")
		if name == "-" && options == "" {
			continue
		}
		value := v.Field(i)
		if field.Anonymous && name == "" {
			embedded := value
			if embedded.Kind() == reflect.Pointer {
				if embedded.IsNil() {
					continue
				}
				embedded = embedded.Elem()
			}
			if embedded.Kind() == reflect.Struct {
				finiteFields(embedded, out)
				continue
			}
		}
		if !field.IsExported() {
			continue
		}
		if name == "" {
			name = field.Name
		}
		if strings.Contains(","+options+",", ",omitempty,") && emptyValue(value) {
			continue
		}
		out[name] = finite(value)
	}
}

// emptyValue tells whether omitempty leaves the value out, as encoding/json does: false, 0, "", nil pointers and
// interfaces, and empty arrays, maps, slices and strings.
func emptyValue(v reflect.Value) bool {
	switch v.Kind() {
	case reflect.Array, reflect.Map, reflect.Slice, reflect.String:
		return v.Len() == 0
	case reflect.Struct:
		return false
	}
	return v.IsZero()
}

func mapKey(k reflect.Value) string {
	switch k.Kind() {
	case reflect.String:
		return k.String()
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return strconv.FormatInt(k.Int(), 10)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return strconv.FormatUint(k.Uint(), 10)
	}
	return fmt.Sprint(k.Interface())
}
