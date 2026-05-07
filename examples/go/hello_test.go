package hello

import "testing"

func TestGreeting(t *testing.T) {
	got := Greeting("praxile")
	want := "hello, praxile"
	if got != want {
		t.Fatalf("Greeting() = %q, want %q", got, want)
	}
}
