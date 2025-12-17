head.ready(function() {
    var CustomVisualizer = function(dispatcher, svgId) {
        Visualizer.call(this, dispatcher, svgId);
    };

    CustomVisualizer.prototype = Object.create(Visualizer.prototype);
    CustomVisualizer.prototype.constructor = CustomVisualizer;

    CustomVisualizer.prototype.renderSpan = function(span) {
        // Call the original renderSpan method
        Visualizer.prototype.renderSpan.call(this, span);

        // Now adjust the position of the label
        var spanGroup = this.svg.group(span.group);
        var textElement = spanGroup.select('text');

        if (!textElement) return;

        // Get the current y position
        var y = parseFloat(textElement.attr('y'));

        // Move the label below the token
        var tokenHeight = 15; // Adjust based on your settings
        var newY = y + tokenHeight;

        textElement.attr('y', newY);
    };

    // Override the Util.embed function to use the CustomVisualizer
    Util.embed = function(target, collData, docData, webFontURLs, callback) {
        var dispatcher = Util.getDispatcher();
        var visualizer = new CustomVisualizer(dispatcher, target);
        dispatcher.post('collectionLoaded', [collData]);
        dispatcher.post('requestRenderData', [docData]);
    };
});
