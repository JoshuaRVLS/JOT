// Overlay placement: which side of the caret a box anchored to it goes on.
//
// The signature popup used to anchor above and, when that ran past the pane's
// first row, pin itself to that row, which drew it over the line being typed on
// the top lines of the window. The choice now lives in one helper, and this pins
// its arithmetic: fit first, roomier side second.
#include "render/overlay_internal.h"
#include <catch2/catch_test_macros.hpp>

using overlay_internal::overlay_box_above;

TEST_CASE("An overlay box takes the side of the caret that fits", "[jot]")
{
  // Room above: the preference, since the completion list owns the space below.
  REQUIRE(overlay_box_above(20, 3, 5, 40)); // above 15, below 19
  // Room only below: the caret on the window's first rows.
  REQUIRE_FALSE(overlay_box_above(6, 3, 5, 40)); // above 1, below 33
  // Space above exactly the box plus its gap counts as fitting.
  REQUIRE(overlay_box_above(10, 3, 5, 40)); // above 5, need 5
  // Neither side fits: the roomier one wins, below then above.
  REQUIRE_FALSE(overlay_box_above(7, 4, 5, 11)); // above 2, below 3
  REQUIRE(overlay_box_above(8, 4, 5, 11));       // above 3, below 2
  // A tie keeps the old preference.
  REQUIRE(overlay_box_above(7, 4, 4, 11)); // above 3, below 3
  // A pane shorter than the box still answers; the caller clamps the result.
  REQUIRE_FALSE(overlay_box_above(5, 10, 5, 8));
}
